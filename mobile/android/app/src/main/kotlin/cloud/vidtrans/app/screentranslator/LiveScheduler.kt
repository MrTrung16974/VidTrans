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
 *   SETTLING ──screen still for settleMs──▶ TRANSLATING ──result──▶ SHOWING
 *      ▲  ▲                                     │ screen changed           │
 *      │  └──────── stale result dropped ◀──────┘                          │
 *      └──────────── screen changed: hide the old translation ◀────────────┘
 *
 * The overlay is always hidden before a frame is captured, so the
 * translator never reads its own output. Changes right after the overlay
 * appears are its own drawing and are ignored for [graceMs].
 */
class LiveScheduler(
    private val settleMs: Long = 350,
    private val minGapMs: Long = 400,
    private val graceMs: Long = 400,
) {
    enum class State { IDLE, SETTLING, TRANSLATING, SHOWING }
    enum class Action { NONE, HIDE_OVERLAY, TRANSLATE }

    var state = State.IDLE
        private set
    private var lastChangeMs = 0L
    private var lastDoneMs = Long.MIN_VALUE / 2
    private var graceUntilMs = Long.MIN_VALUE / 2
    private var stale = false

    val active get() = state != State.IDLE

    fun start(nowMs: Long) {
        state = State.SETTLING
        lastChangeMs = nowMs
        stale = false
    }

    fun stop() {
        state = State.IDLE
    }

    /** Translate as soon as possible (button tapped while live). */
    fun forceNow(nowMs: Long): Action {
        if (!active) return Action.NONE
        val wasShowing = state == State.SHOWING
        if (state == State.TRANSLATING) stale = true
        if (state != State.TRANSLATING) state = State.SETTLING
        lastChangeMs = nowMs - settleMs
        lastDoneMs = Long.MIN_VALUE / 2
        return if (wasShowing) Action.HIDE_OVERLAY else Action.NONE
    }

    /** The screen content changed (two consecutive frames differ). */
    fun onScreenChanged(nowMs: Long): Action = when (state) {
        State.IDLE -> Action.NONE
        State.SHOWING ->
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
            stale = true
            lastChangeMs = nowMs
            Action.NONE
        }
    }

    /** Called every ~100 ms while live. */
    fun onTick(nowMs: Long): Action {
        if (state != State.SETTLING) return Action.NONE
        if (nowMs - lastChangeMs < settleMs || nowMs - lastDoneMs < minGapMs) return Action.NONE
        state = State.TRANSLATING
        stale = false
        return Action.TRANSLATE
    }

    /**
     * A translation finished. True when it should be shown; false when the
     * screen changed meanwhile (positions are outdated) or live was stopped.
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
