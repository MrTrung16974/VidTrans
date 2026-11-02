package cloud.vidtrans.app.screentranslator

import android.content.Context
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.os.Build
import android.os.Handler
import android.util.DisplayMetrics
import android.view.WindowManager

/** One captured screen: tightly packed RGBA pixels. */
class CapturedFrame(val width: Int, val height: Int, val rgba: ByteArray) {
    /** True when (almost) every sampled pixel is black: the app on screen
     * blocks capture (FLAG_SECURE), e.g. banking or DRM video. */
    fun looksBlank(): Boolean {
        var lit = 0
        var samples = 0
        val step = maxOf(1, (width * height) / 4000)
        var i = 0
        while (i < width * height) {
            val o = i * 4
            samples++
            if ((rgba[o].toInt() and 0xFF) + (rgba[o + 1].toInt() and 0xFF) + (rgba[o + 2].toInt() and 0xFF) > 24) lit++
            i += step
        }
        return lit < samples / 200
    }
}

/**
 * Mirrors the screen into an ImageReader for the whole session. Android 14+
 * allows a single VirtualDisplay per MediaProjection, so it is created once
 * and only resized on rotation. Frames arrive only when the screen changes;
 * the newest one is kept and older ones are closed straight away.
 */
class ScreenCapture(
    private val context: Context,
    private val projection: MediaProjection,
    private val handler: Handler,
) {
    private var reader: ImageReader? = null
    private var display: VirtualDisplay? = null
    private var latest: Image? = null
    private var width = 0
    private var height = 0

    /** Live mode: told about every new frame (on the capture thread). */
    @Volatile
    var onFrame: ((FrameSignature) -> Unit)? = null

    private fun keep(image: Image?) {
        if (image == null) return
        val listener = onFrame
        val signature = if (listener != null) signature(image) else null
        synchronized(this) {
            latest?.close()
            latest = image
        }
        if (signature != null) listener?.invoke(signature)
    }

    /** 72-column grayscale thumbnail of [image] for change detection. */
    private fun signature(image: Image): FrameSignature {
        val plane = image.planes[0]
        val buffer = plane.buffer
        val w = image.width
        val h = image.height
        val cols = 72
        val rows = maxOf(1, cols * h / maxOf(1, w))
        val luma = ByteArray(cols * rows)
        for (r in 0 until rows) {
            val y = ((r + 0.5f) * h / rows).toInt()
            for (c in 0 until cols) {
                val x = ((c + 0.5f) * w / cols).toInt()
                val i = y * plane.rowStride + x * plane.pixelStride
                val red = buffer.get(i).toInt() and 0xFF
                val green = buffer.get(i + 1).toInt() and 0xFF
                val blue = buffer.get(i + 2).toInt() and 0xFF
                luma[r * cols + c] = ((red * 299 + green * 587 + blue * 114) / 1000).toByte()
            }
        }
        return FrameSignature(w, h, cols, rows, luma, image.timestamp)
    }

    private fun realMetrics(): Triple<Int, Int, Int> {
        val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val dpi = context.resources.displayMetrics.densityDpi
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val bounds = wm.maximumWindowMetrics.bounds
            Triple(bounds.width(), bounds.height(), dpi)
        } else {
            val metrics = DisplayMetrics()
            @Suppress("DEPRECATION")
            wm.defaultDisplay.getRealMetrics(metrics)
            Triple(metrics.widthPixels, metrics.heightPixels, dpi)
        }
    }

    private fun newReader(w: Int, h: Int): ImageReader =
        ImageReader.newInstance(w, h, PixelFormat.RGBA_8888, 3).apply {
            setOnImageAvailableListener({ keep(it.acquireLatestImage()) }, handler)
        }

    /** Starts mirroring, or follows a rotation / resolution change. */
    @Synchronized
    fun ensureStarted() {
        val (w, h, dpi) = realMetrics()
        if (display != null && w == width && h == height) return
        val next = newReader(w, h)
        val current = display
        if (current == null) {
            display = projection.createVirtualDisplay(
                "VidTransScreenTranslator", w, h, dpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                next.surface, null, handler,
            )
        } else {
            current.resize(w, h, dpi)
            current.surface = next.surface
        }
        latest?.close()
        latest = null
        reader?.close()
        reader = next
        width = w
        height = h
    }

    /**
     * The first frame composed after [sinceNanos] (System.nanoTime), i.e.
     * after the floating button was hidden; the newest frame when the screen
     * did not change within [timeoutMs].
     */
    fun frameAfter(sinceNanos: Long, timeoutMs: Long = 700): CapturedFrame? {
        val deadline = System.nanoTime() + timeoutMs * 1_000_000
        while (System.nanoTime() < deadline) {
            synchronized(this) {
                val image = latest
                if (image != null && image.timestamp > sinceNanos) return copy(image)
            }
            Thread.sleep(16)
        }
        synchronized(this) { return latest?.let { copy(it) } }
    }

    private fun copy(image: Image): CapturedFrame {
        val plane = image.planes[0]
        val buffer = plane.buffer.duplicate()
        val w = image.width
        val h = image.height
        val rowBytes = w * 4
        val out = ByteArray(rowBytes * h)
        if (plane.rowStride == rowBytes && plane.pixelStride == 4) {
            buffer.position(0)
            buffer.get(out, 0, out.size)
        } else {
            for (row in 0 until h) {
                buffer.position(row * plane.rowStride)
                buffer.get(out, row * rowBytes, rowBytes)
            }
        }
        return CapturedFrame(w, h, out)
    }

    /** Current capture size (width, height), or null before start. */
    @Synchronized
    fun size(): Pair<Int, Int>? = if (display == null) null else width to height

    @Synchronized
    fun release() {
        latest?.close()
        latest = null
        display?.release()
        display = null
        reader?.close()
        reader = null
    }
}
