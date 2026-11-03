package cloud.vidtrans.app.screentranslator

import kotlin.math.abs
import kotlin.math.max

/** A screen rectangle in pixels (kept free of android.graphics for JVM tests). */
data class PixelRect(val left: Int, val top: Int, val right: Int, val bottom: Int) {
    fun contains(x: Int, y: Int) = x in left until right && y in top until bottom
    fun toList() = listOf(left, top, right, bottom)
}

/**
 * A tiny grayscale thumbnail of one frame: [cols] × [rows] samples of the
 * [width] × [height] screen. Comparing two of them tells whether the screen
 * content changed (scroll, new message, page switch) at a fraction of the
 * cost of comparing whole frames.
 */
class FrameSignature(
    val width: Int,
    val height: Int,
    val cols: Int,
    val rows: Int,
    val luma: ByteArray,
    val timestampNanos: Long,
    /** Full-resolution row summary of the content area, for scroll tracking. */
    val profile: RowProfile? = null,
) {
    /** Screen x/y of the sample in column [c] / row [r]. */
    fun sampleX(c: Int) = ((c + 0.5f) * width / cols).toInt()
    fun sampleY(r: Int) = ((r + 0.5f) * height / rows).toInt()

    /**
     * Whether [other] shows different content, ignoring samples inside
     * [ignore] (system bars, the floating button). A blinking cursor or a
     * ticking counter changes a few samples; that is not worth a new
     * translation, so at least [minFraction] of the samples must differ.
     */
    fun differsFrom(other: FrameSignature, ignore: List<PixelRect>, minFraction: Float = 0.003f): Boolean {
        if (other.width != width || other.height != height || other.cols != cols || other.rows != rows) return true
        var compared = 0
        var changed = 0
        for (r in 0 until rows) {
            val y = sampleY(r)
            for (c in 0 until cols) {
                val x = sampleX(c)
                if (ignore.any { it.contains(x, y) }) continue
                compared++
                val i = r * cols + c
                if (abs((luma[i].toInt() and 0xFF) - (other.luma[i].toInt() and 0xFF)) > 20) changed++
            }
        }
        return compared > 0 && changed > max(3f, compared * minFraction)
    }
}

/**
 * Live (continuous) translation, decoupled from Android so it is unit tested:
 *
 *   SETTLING ──still settleMs──▶ TRANSLATING (full) ──result──▶ SHOWING
 *                                                              │   ▲
 *                                       content scrolled       ▼   │ result
 *                                   TRACKING ──still──▶ TRANSLATING (more)
 *
 * While SHOWING, a scroll does not hide the translation: the overlay moves
 * with the content (TRACKING) and, once the screen is still, only the text
 * that scrolled in is read and translated (TRANSLATE_MORE) — no flicker.
 * Any other change (new page, dialog) hides it and starts over (SETTLING →
 * full TRANSLATE, overlay hidden so the translator never reads its output).
 * Changes right after the overlay was redrawn are its own drawing and are
 * ignored for [graceMs]. A result that arrives after a non-scroll change is
 * dropped; after a scroll it is moved by the caller instead.
 */
class LiveScheduler(
    private val settleMs: Long = 350,
    private val moreSettleMs: Long = 160,
    private val minGapMs: Long = 400,
    private val graceMs: Long = 400,
) {
    enum class State { IDLE, SETTLING, TRANSLATING, SHOWING, TRACKING }
    enum class Action { NONE, HIDE_OVERLAY, TRANSLATE, TRANSLATE_MORE }

    var state = State.IDLE
        private set

    /** The running translation keeps the overlay (true) or replaces a hidden one. */
    var incremental = false
        private set
    private var lastChangeMs = 0L
    private var lastDoneMs = Long.MIN_VALUE / 2
    private var graceUntilMs = Long.MIN_VALUE / 2
    private var stale = false
    private var lastScrollMs = Long.MIN_VALUE / 2

    /**
     * How long the screen must stay still after a scroll before it counts as
     * stopped: 2.5 frame intervals of the scroll itself (a phone at 60 fps
     * settles in [moreSettleMs]; a slow device needs longer).
     */
    private var scrollSettleMs = moreSettleMs

    val active get() = state != State.IDLE

    fun start(nowMs: Long) {
        state = State.SETTLING
        lastChangeMs = nowMs
        stale = false
    }

    fun stop() {
        state = State.IDLE
    }

    /** Translate everything again as soon as possible (button tapped while live). */
    fun forceNow(nowMs: Long): Action {
        if (!active) return Action.NONE
        val hadOverlay = state == State.SHOWING || state == State.TRACKING ||
            (state == State.TRANSLATING && incremental)
        if (state == State.TRANSLATING) stale = true
        if (state != State.TRANSLATING) state = State.SETTLING
        lastChangeMs = nowMs - settleMs
        lastDoneMs = Long.MIN_VALUE / 2
        return if (hadOverlay) Action.HIDE_OVERLAY else Action.NONE
    }

    /** Our own overlay was drawn within the grace period. */
    fun inGrace(nowMs: Long) = nowMs < graceUntilMs

    /** Start over with a full translation as soon as possible (old translations dropped). */
    fun restart(nowMs: Long) {
        if (!active) return
        state = State.SETTLING
        stale = false
        lastChangeMs = nowMs - settleMs
    }

    /** The overlay was redrawn by us (new or moved regions): ignore the next frames. */
    fun onOverlayDrawn(nowMs: Long) {
        graceUntilMs = nowMs + graceMs
    }

    /** The content scrolled by a known distance and the overlay was moved with it. */
    fun onScrolled(nowMs: Long) {
        val interval = nowMs - lastScrollMs
        if (interval in 1..1000) scrollSettleMs = (interval * 5 / 2).coerceIn(moreSettleMs, 800)
        lastScrollMs = nowMs
        when (state) {
            State.SHOWING, State.TRACKING -> {
                state = State.TRACKING
                lastChangeMs = nowMs
            }
            State.TRANSLATING -> lastChangeMs = nowMs // caller moves the result too
            else -> Unit
        }
    }

    /** The screen content changed in a way that is not a scroll. */
    fun onScreenChanged(nowMs: Long): Action = when (state) {
        State.IDLE -> Action.NONE
        State.SHOWING, State.TRACKING ->
            if (nowMs < graceUntilMs) Action.NONE // our own overlay being drawn
            else {
                state = State.SETTLING
                lastChangeMs = nowMs
                Action.HIDE_OVERLAY
            }
        State.SETTLING -> {
            lastChangeMs = nowMs
            Action.NONE
        }
        State.TRANSLATING -> {
            if (incremental && nowMs < graceUntilMs) {
                Action.NONE
            } else {
                stale = true
                lastChangeMs = nowMs
                // An incremental run still shows the old overlay: it is wrong now.
                if (incremental) Action.HIDE_OVERLAY else Action.NONE
            }
        }
    }

    /** Called every ~100 ms while live. */
    fun onTick(nowMs: Long): Action {
        val settle = when (state) {
            State.SETTLING -> settleMs
            State.TRACKING -> scrollSettleMs
            else -> return Action.NONE
        }
        if (nowMs - lastChangeMs < settle || nowMs - lastDoneMs < minGapMs) return Action.NONE
        incremental = state == State.TRACKING
        state = State.TRANSLATING
        stale = false
        return if (incremental) Action.TRANSLATE_MORE else Action.TRANSLATE
    }

    /**
     * A translation finished. True when it should be shown; false when the
     * screen changed meanwhile in a way that is not a scroll, or live stopped.
     */
    fun onTranslated(nowMs: Long): Boolean {
        lastDoneMs = nowMs
        if (state != State.TRANSLATING) return false
        if (stale) {
            state = State.SETTLING
            return false
        }
        state = State.SHOWING
        graceUntilMs = nowMs + graceMs
        return true
    }

    /** The translation failed: wait for the next change instead of retrying in a loop. */
    fun onFailed(nowMs: Long) {
        lastDoneMs = nowMs
        if (state == State.TRANSLATING) {
            state = State.SHOWING
            graceUntilMs = nowMs + graceMs
        }
    }
}
