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

private fun overlayType(): Int =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
    else @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE

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
class FloatingBubble(context: Context, private val onTap: () -> Unit) : FrameLayout(context) {
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
        context.dp(58f), context.dp(58f), overlayType(),
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP or Gravity.START
        x = context.resources.displayMetrics.widthPixels - context.dp(72f)
        y = context.resources.displayMetrics.heightPixels / 3
    }
    var attached = false
        private set

    init {
        background = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(Color.parseColor("#EA580C"))
            setStroke(context.dp(2f), Color.WHITE)
        }
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
        setOnTouchListener { _, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX; downY = event.rawY
                    startX = params.x; startY = params.y
                    dragging = false
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - downX
                    val dy = event.rawY - downY
                    if (dragging || abs(dx) > slop || abs(dy) > slop) {
                        dragging = true
                        params.x = (startX + dx).toInt()
                        params.y = (startY + dy).toInt()
                        if (attached) windowManager.updateViewLayout(this, params)
                    }
                }
                MotionEvent.ACTION_UP -> if (!dragging && !busy) onTap()
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
    private val regions: List<OverlayRegion>,
    private val note: String,
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

    private val params = WindowManager.LayoutParams(
        WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT, overlayType(),
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP or Gravity.START
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }
    }

    init {
        setOnTouchListener { _, event ->
            if (event.actionMasked == MotionEvent.ACTION_UP) onClose()
            true
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
        for (region in regions) {
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
        }
        canvas.restore()

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
