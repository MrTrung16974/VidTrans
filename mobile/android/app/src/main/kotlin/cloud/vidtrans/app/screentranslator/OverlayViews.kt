package cloud.vidtrans.app.screentranslator

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.ProgressBar
import android.widget.TextView
import kotlin.math.abs
import kotlin.math.max

/**
 * [trusted]: drawn by our AccessibilityService. Accessibility overlays are
 * trusted windows: Android lets touches through them at full opacity, so the
 * translation can cover the original text completely and the app below still
 * scrolls and taps (an ordinary app overlay must stay ≤ 80% opaque for that).
 */
private fun overlayType(trusted: Boolean = false): Int = when {
    trusted -> WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
    Build.VERSION.SDK_INT >= Build.VERSION_CODES.O -> WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
    else -> @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE
}

private fun Context.dp(value: Float): Int = (value * resources.displayMetrics.density).toInt()

/** One translated block, in screen pixels (from the Dart pipeline). */
class OverlayRegion(
    val text: String,
    val paint: RectF,
    val lines: List<RectF>,
    val fontSize: Float,
    val background: Int,
    val foreground: Int,
) {
    companion object {
        @Suppress("UNCHECKED_CAST")
        private fun rect(value: Any?): RectF {
            val v = (value as List<Number>).map { it.toFloat() }
            return RectF(v[0], v[1], v[2], v[3])
        }

        @Suppress("UNCHECKED_CAST")
        fun fromMap(map: Map<String, Any?>): OverlayRegion = OverlayRegion(
            text = map["text"] as String,
            paint = rect(map["paint"]),
            lines = (map["lines"] as List<Any?>).map { rect(it) },
            fontSize = (map["size"] as Number).toFloat(),
            background = (map["bg"] as Number).toInt(),
            foreground = (map["fg"] as Number).toInt(),
        )
    }
}

/**
 * Draggable round button floating over other apps. Tap = translate the
 * screen; the service hides it while capturing so it is not in the frame.
 */
@SuppressLint("ViewConstructor", "ClickableViewAccessibility")
class FloatingBubble(
    context: Context,
    private val onTap: () -> Unit,
    private val onLongPress: () -> Unit,
    trusted: Boolean = false,
) : FrameLayout(context) {
    private val windowManager = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val label = TextView(context).apply {
        text = "Dịch"
        setTextColor(Color.WHITE)
        textSize = 14f
        typeface = Typeface.DEFAULT_BOLD
        gravity = Gravity.CENTER
    }
    private val spinner = ProgressBar(context).apply {
        isIndeterminate = true
        visibility = GONE
        indeterminateTintList = android.content.res.ColorStateList.valueOf(Color.WHITE)
    }
    val params = WindowManager.LayoutParams(
        context.dp(58f), context.dp(58f), overlayType(trusted),
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP or Gravity.START
        x = context.resources.displayMetrics.widthPixels - context.dp(72f)
        y = context.resources.displayMetrics.heightPixels / 3
    }
    var attached = false
        private set

    private val circle = GradientDrawable().apply {
        shape = GradientDrawable.OVAL
        setColor(Color.parseColor("#EA580C"))
        setStroke(context.dp(2f), Color.WHITE)
    }

    /** Live (continuous) mode: teal "LIVE" instead of the orange "Dịch". */
    var live = false
        set(value) {
            field = value
            circle.setColor(Color.parseColor(if (value) "#0F766E" else "#EA580C"))
            label.text = if (value) "LIVE" else "Dịch"
        }

    /** Where the button is on screen, so live translation can ignore it. */
    fun screenBounds(): PixelRect? {
        if (!attached || width == 0) return null
        val at = IntArray(2)
        getLocationOnScreen(at)
        return PixelRect(at[0], at[1], at[0] + width, at[1] + height)
    }

    init {
        background = circle
        elevation = context.dp(6f).toFloat()
        contentDescription = "VidTrans: dịch màn hình"
        addView(label, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
        val pad = context.dp(14f)
        addView(spinner, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT).apply {
            setMargins(pad, pad, pad, pad)
        })
        val slop = ViewConfiguration.get(context).scaledTouchSlop
        var downX = 0f
        var downY = 0f
        var startX = 0
        var startY = 0
        var dragging = false
        var longPressed = false
        val longPress = Runnable {
            if (!dragging) {
                longPressed = true
                performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS)
                onLongPress()
            }
        }
        setOnTouchListener { _, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX; downY = event.rawY
                    startX = params.x; startY = params.y
                    dragging = false
                    longPressed = false
                    postDelayed(longPress, ViewConfiguration.getLongPressTimeout().toLong())
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - downX
                    val dy = event.rawY - downY
                    if (dragging || abs(dx) > slop || abs(dy) > slop) {
                        dragging = true
                        removeCallbacks(longPress)
                        params.x = (startX + dx).toInt()
                        params.y = (startY + dy).toInt()
                        if (attached) windowManager.updateViewLayout(this, params)
                    }
                }
                MotionEvent.ACTION_UP -> {
                    removeCallbacks(longPress)
                    if (!dragging && !longPressed && (!busy || live)) onTap()
                }
                MotionEvent.ACTION_CANCEL -> removeCallbacks(longPress)
            }
            true
        }
    }

    var busy = false
        set(value) {
            field = value
            label.visibility = if (value) GONE else VISIBLE
            spinner.visibility = if (value) VISIBLE else GONE
        }

    fun show() {
        if (!attached) {
            windowManager.addView(this, params)
            attached = true
        }
        visibility = VISIBLE
    }

    /** Invisible but kept in place, so it reappears where the user left it. */
    fun hideForCapture() {
        visibility = INVISIBLE
    }

    fun remove() {
        if (attached) windowManager.removeViewImmediate(this)
        attached = false
    }
}

/**
 * Full-screen layer that covers each original text block with its sampled
 * background and draws the translation. Tapping anywhere closes it, so the
 * app below is never blocked for longer than the user wants (on-demand mode;
 * Android 12+ would block touches through an opaque overlay anyway).
 */
@SuppressLint("ViewConstructor", "ClickableViewAccessibility")
class TranslationOverlay(
    context: Context,
    regions: List<OverlayRegion>,
    private val note: String?,
    /**
     * False in live mode: touches pass through to the app below. Android 12+
     * only lets touches through an overlay of another app whose opacity is at
     * most 0.8, so the live layer is drawn at 80%.
     */
    private val interactive: Boolean,
    /** Drawn by the accessibility service: opaque and still touch-through. */
    private val trusted: Boolean = false,
    private val onClose: () -> Unit,
) : View(context) {
    private val windowManager = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val cover = Paint(Paint.ANTI_ALIAS_FLAG)
    private val textPaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
    }
    private val hintPaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textSize = context.dp(13f).toFloat()
        typeface = Typeface.DEFAULT_BOLD
    }
    private val hintBackground = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.argb(215, 15, 23, 42) }
    private val location = IntArray(2)
    private val layouts = HashMap<OverlayRegion, StaticLayout>()

    /**
     * A translation placed relative to the live "base" frame (a clean frame,
     * captured with nothing of ours on screen): its box is in the coordinates
     * of the frame it was read from, [anchorDy] moves it into base-frame
     * coordinates, [dy] is where it is drawn now.
     */
    private class Placed(val region: OverlayRegion, val anchorDy: Float, var dy: Float)

    private val placed = regions.map { Placed(it, 0f, 0f) }.toMutableList()

    /**
     * Screen rows where translations may be drawn (between the system bars):
     * a translation scrolled under the status bar must not cover it.
     */
    var clipTop = 0f
    var clipBottom = Float.MAX_VALUE

    /**
     * Called after every draw. The next captured frames show this drawing,
     * which live mode must not mistake for the page changing.
     */
    var onDrawn: (() -> Unit)? = null

    /**
     * While the screen changes (scrolling) the translations are hidden but
     * kept; when it is still again they are placed by one measurement against
     * the base frame and reappear at once.
     */
    var parked: Boolean
        get() = visibility != VISIBLE
        set(value) {
            visibility = if (value) INVISIBLE else VISIBLE
        }

    val isEmpty get() = placed.isEmpty()

    /**
     * Places every translation for content that moved [total] pixels since
     * the base frame; [isFixed] rows (toolbars) did not move. Translations now
     * out of sight are forgotten.
     */
    /** Returns how many translations were dropped because their text was not found. */
    fun place(
        total: Int,
        isFixed: (Float) -> Boolean,
        /** (top, bottom) at base, move → is the same text under it now? */
        stillThere: (Float, Float, Int) -> Boolean = { _, _, _ -> true },
    ): Int {
        val wrong = ArrayList<Placed>()
        for (p in placed) {
            val atBase = p.region.paint.centerY() + p.anchorDy
            val move = if (isFixed(atBase)) 0 else total
            p.dy = p.anchorDy + move
            val top = p.region.paint.top + p.anchorDy
            if (!stillThere(top, top + p.region.paint.height(), move)) wrong.add(p)
        }
        // Text that vanished or moved differently: drop it, it is read again.
        placed.removeAll(wrong.toSet())
        placed.removeAll { it.region.paint.bottom + it.dy < clipTop || it.region.paint.top + it.dy > clipBottom }
        invalidate()
        return wrong.size
    }

    /** The frame they are drawn on now becomes the base: anchors take the current positions. */
    fun rebase() {
        val moved = placed.map { Placed(it.region, it.dy, it.dy) }
        placed.clear()
        placed.addAll(moved)
    }

    /**
     * Adds translations read from a frame in which the content had moved
     * [total] pixels since the base frame (they are drawn where they were
     * read). An older translation overlapping a new one is dropped: the new
     * one was read from the screen just now.
     */
    fun add(regions: List<OverlayRegion>, total: Int) {
        val fresh = regions.map { Placed(it, -total.toFloat(), 0f) }
        placed.removeAll { old -> fresh.any { overlapShare(old, it) > 0.3f } }
        placed.addAll(fresh)
        invalidate()
    }

    /**
     * Shows exactly [regions] (accessibility mode: positions come straight
     * from the app's view tree on every change, nothing to track).
     */
    fun replaceAll(regions: List<OverlayRegion>) {
        placed.clear()
        placed.addAll(regions.map { Placed(it, 0f, 0f) })
        invalidate()
    }

    /** Overlap as a share of the smaller of the two boxes. */
    private fun overlapShare(a: Placed, b: Placed): Float {
        val ra = RectF(a.region.paint).apply { offset(0f, a.dy) }
        val rb = RectF(b.region.paint).apply { offset(0f, b.dy) }
        val i = RectF()
        if (!i.setIntersect(ra, rb)) return 0f
        val smaller = minOf(ra.width() * ra.height(), rb.width() * rb.height())
        return if (smaller <= 0f) 0f else i.width() * i.height() / smaller
    }

    /** Where translations are now (screen pixels), so new OCR skips them. */
    fun boxes(margin: Int): List<PixelRect> = placed.map { p ->
        val r = p.region.paint
        val y = p.dy
        PixelRect(
            (r.left - margin).toInt(), (r.top + y - margin).toInt(),
            (r.right + margin).toInt(), (r.bottom + y + margin).toInt(),
        )
    }

    private val params = WindowManager.LayoutParams(
        WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT, overlayType(trusted),
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP or Gravity.START
        windowAnimations = 0 // appear at once: live mode compares frames
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }
        if (!interactive) {
            flags = flags or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
            alpha = if (trusted) {
                1f
            } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val input = context.getSystemService(Context.INPUT_SERVICE) as android.hardware.input.InputManager
                minOf(0.8f, input.maximumObscuringOpacityForTouch)
            } else {
                0.8f
            }
        }
    }

    init {
        if (interactive) {
            setOnTouchListener { _, event ->
                if (event.actionMasked == MotionEvent.ACTION_UP) onClose()
                true
            }
        }
    }

    fun show() = windowManager.addView(this, params)

    fun remove() {
        if (isAttachedToWindow) windowManager.removeViewImmediate(this)
    }

    /** Line height = 1.18 × font size, as the Dart layout measured it. */
    private fun buildLayout(region: OverlayRegion, size: Float): StaticLayout {
        val paint = TextPaint(textPaint).apply {
            textSize = size
            color = region.foreground
        }
        val natural = paint.fontMetrics.let { it.descent - it.ascent }
        val multiplier = if (natural > 0) (size * 1.18f) / natural else 1f
        val width = max(1, region.paint.width().toInt())
        return StaticLayout.Builder.obtain(region.text, 0, region.text.length, paint, width)
            .setAlignment(Layout.Alignment.ALIGN_NORMAL)
            .setLineSpacing(0f, multiplier)
            .setIncludePad(false)
            .build()
    }

    /**
     * Android may wrap a word earlier than Flutter measured; shrink a little
     * (down to 75%) rather than spill out of the bubble.
     */
    private fun layoutFor(region: OverlayRegion): StaticLayout = layouts.getOrPut(region) {
        var size = region.fontSize
        var layout = buildLayout(region, size)
        while (layout.height > region.paint.height() + 1f && size > region.fontSize * 0.75f) {
            size *= 0.95f
            layout = buildLayout(region, size)
        }
        layout
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        // Regions are in screen pixels; this window may start below the
        // status bar or beside a cutout.
        getLocationOnScreen(location)
        canvas.save()
        canvas.translate(-location[0].toFloat(), -location[1].toFloat())
        canvas.clipRect(0f, clipTop, Float.MAX_VALUE / 4, clipBottom)
        for (p in placed) {
            val region = p.region
            canvas.save()
            canvas.translate(0f, p.dy)
            cover.color = region.background
            val radius = region.fontSize * 0.2f
            // Glyph edges reach a little past the OCR boxes.
            val grow = -max(3f, region.fontSize * 0.12f)
            for (line in region.lines) {
                canvas.drawRoundRect(RectF(line).apply { inset(grow, grow) }, radius, radius, cover)
            }
            canvas.drawRoundRect(RectF(region.paint).apply { inset(-2f, -2f) }, radius, radius, cover)
            val layout = layoutFor(region)
            val dy = max(0f, (region.paint.height() - layout.height) / 2f)
            canvas.save()
            canvas.translate(region.paint.left, region.paint.top + dy)
            layout.draw(canvas)
            canvas.restore()
            canvas.restore()
        }
        canvas.restore()
        onDrawn?.invoke()

        val note = note ?: return
        val textWidth = hintPaint.measureText(note)
        val padX = context.dp(14f).toFloat()
        val padY = context.dp(9f).toFloat()
        val left = (width - textWidth) / 2f - padX
        val bottom = height - context.dp(48f).toFloat()
        val top = bottom - hintPaint.textSize - padY * 2
        canvas.drawRoundRect(RectF(left, top, left + textWidth + padX * 2, bottom), 99f, 99f, hintBackground)
        canvas.drawText(note, left + padX, bottom - padY - hintPaint.descent(), hintPaint)
    }
}
