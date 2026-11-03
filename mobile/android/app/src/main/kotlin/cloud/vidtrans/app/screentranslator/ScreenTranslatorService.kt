package cloud.vidtrans.app.screentranslator

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.util.Log
import android.widget.Toast
import cloud.vidtrans.app.MainActivity
import cloud.vidtrans.app.R
import io.flutter.FlutterInjector
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.engine.dart.DartExecutor
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer

/**
 * Phase 2 of the screen translator: a floating "Dịch" button over other
 * apps. Tapping it captures one frame (MediaProjection), translates it in a
 * headless Flutter engine running the same pipeline as "Dịch ảnh", and shows
 * the translation where the original text was until the user taps.
 *
 * Nothing is stored: the frame lives in memory and one temporary JPEG for
 * OCR, which the engine deletes once read.
 */
class ScreenTranslatorService : Service() {
    companion object {
        private const val TAG = "ScreenTranslator"
        const val ACTION_START = "cloud.vidtrans.screen_translator.START"
        const val ACTION_STOP = "cloud.vidtrans.screen_translator.STOP"
        const val ACTION_SETTINGS = "cloud.vidtrans.screen_translator.SETTINGS"
        const val EXTRA_RESULT_CODE = "resultCode"
        const val EXTRA_RESULT_DATA = "resultData"
        const val EXTRA_SOURCE = "source"
        const val EXTRA_TARGET = "target"
        const val EXTRA_MODE = "mode"
        const val EXTRA_LIVE = "live"
        private const val CHANNEL_ID = "screen_translator"
        private const val NOTIFICATION_ID = 7301
        private const val ENGINE_CHANNEL = "vidtrans/screen_translator_engine"
        /** A translation that has not answered by then is reported as failed. */
        private const val TRANSLATE_TIMEOUT_MS = 45_000L
        private const val ENGINE_LIBRARY =
            "package:vidtrans_mobile/features/screen_translator/screen_translator_engine.dart"

        @Volatile
        var running = false
            private set

        /** Live (continuous) translation on; the button's long press flips it. */
        @Volatile
        var liveNow = false
            private set
    }

    private val main = Handler(Looper.getMainLooper())
    private lateinit var worker: HandlerThread
    private lateinit var workerHandler: Handler
    private var projection: MediaProjection? = null
    private var capture: ScreenCapture? = null
    private var bubble: FloatingBubble? = null
    private var overlay: TranslationOverlay? = null
    private var engine: FlutterEngine? = null
    private var engineChannel: MethodChannel? = null
    private var engineReady = false
    private val pendingCalls = ArrayList<() -> Unit>()
    private var source = "auto"
    private var target = "vi"
    private var mode = "online"
    private var live = false

    // Live mode (phase 3): see LiveScheduler. Grace counts from each real draw
    // of the overlay (see onDrawn), so a slow device that composes it late does
    // not take our own drawing for the page changing.
    private val scheduler = LiveScheduler(graceMs = 500)
    @Volatile private var previousFrame: FrameSignature? = null
    @Volatile private var ignoreAreas: List<PixelRect> = emptyList()
    private var overlayHiddenAtNanos = 0L

    /** Live overlay drawn on screen. */
    @Volatile private var overlayShown = false

    /** Live overlay exists (drawn, or parked while the screen changes). */
    @Volatile private var overlayTracking = false

    /** A full live translation is running (nothing of ours on screen). */
    @Volatile private var translating = false

    /**
     * The clean frame (captured with nothing of ours on screen) every live
     * translation is placed against, and how far the content has moved since
     * it at the last measurement. Measuring "base → now" in one go, between
     * two frames that both show all text, cannot drift and cannot mistake one
     * chat bubble for its look-alike neighbour.
     */
    @Volatile private var base: RowProfile? = null
    private var baseTotal = 0

    /** Base frame of the running full translation (its capture). */
    @Volatile private var pendingBase: RowProfile? = null

    /** [baseTotal] when the running incremental translation captured its frame. */
    private var totalAtCapture = 0

    /** Content scroll during a running full translation, from clean frames. */
    private var scrollSinceCapture = 0

    /** Net content move at the last settle: where new text appeared. */
    private var movedAtSettle = 0

    /** The last settle dropped translations: the next read covers the whole screen. */
    private var readEverything = false

    /** When the screen last changed under the overlay (main thread clock). */
    private var lastScrollMs = Long.MIN_VALUE / 2

    /** Last frame's scroll: flings are smooth, so it predicts the next one. */
    @Volatile private var lastDy: Int? = null
    private var translateStartedMs = 0L
    private var lastLiveError = ""
    private val ticker = object : Runnable {
        override fun run() {
            if (!scheduler.active) return
            ignoreAreas = currentIgnoreAreas()
            act(scheduler.onTick(nowMs()))
            main.postDelayed(this, 100)
        }
    }

    private fun nowMs() = android.os.SystemClock.uptimeMillis()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        worker = HandlerThread("ScreenTranslatorCapture").apply { start() }
        workerHandler = Handler(worker.looper)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> stopSelf()
            ACTION_SETTINGS -> readSettings(intent)
            ACTION_START -> start(intent)
        }
        return START_NOT_STICKY
    }

    private fun readSettings(intent: Intent) {
        val changed = intent.getStringExtra(EXTRA_SOURCE).let { it != null && it != source } ||
            intent.getStringExtra(EXTRA_TARGET).let { it != null && it != target } ||
            intent.getStringExtra(EXTRA_MODE).let { it != null && it != mode }
        source = intent.getStringExtra(EXTRA_SOURCE) ?: source
        target = intent.getStringExtra(EXTRA_TARGET) ?: target
        mode = intent.getStringExtra(EXTRA_MODE) ?: mode
        intent.getStringExtra(EXTRA_LIVE)?.let { live = it == "true" }
        if (projection != null) {
            applyLive()
            if (changed && scheduler.active) act(scheduler.forceNow(nowMs())) // new languages: redo now
        }
    }

    private fun start(intent: Intent) {
        readSettings(intent)
        if (projection != null) return
        // Android 14+: the service must be in the foreground with the
        // mediaProjection type before the projection is created.
        val notification = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        val data: Intent? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
        } else {
            @Suppress("DEPRECATION") intent.getParcelableExtra(EXTRA_RESULT_DATA)
        }
        val code = intent.getIntExtra(EXTRA_RESULT_CODE, Activity.RESULT_CANCELED)
        val manager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        val granted = data?.let { manager.getMediaProjection(code, it) }
        if (granted == null) {
            Log.w(TAG, "screen capture consent missing")
            stopSelf()
            return
        }
        // Must be registered before createVirtualDisplay on Android 14+.
        granted.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                main.post { stopSelf() } // stopped from the system UI / status bar chip
            }
        }, main)
        projection = granted
        capture = ScreenCapture(this, granted, workerHandler).also { it.ensureStarted() }
        startEngine()
        bubble = FloatingBubble(
            this,
            onTap = { onBubbleTap() },
            onLongPress = { toggleLive() },
            onDismiss = { main.post { stopSelf() } }, // dropped on the bin
        ).also { button ->
            button.show()
            button.post {
                val at = IntArray(2)
                button.getLocationOnScreen(at)
                // Read by scripts/test_screen_translator.sh to tap the button.
                Log.i(TAG, "bubble at ${at[0]},${at[1]},${button.width},${button.height}")
            }
        }
        running = true
        applyLive()
    }

    private fun onBubbleTap() {
        if (live) act(scheduler.forceNow(nowMs())) else translateScreen()
    }

    private fun toggleLive() {
        live = !live
        applyLive()
        toast(if (live) "Đã bật dịch liên tục (LIVE)" else "Đã chuyển về chạm để dịch")
    }

    /** Starts or stops live translation to match [live]. */
    private fun applyLive() {
        val screen = capture ?: return
        liveNow = live
        bubble?.live = live
        if (live == scheduler.active) return
        setOverlay(null)
        bubble?.show()
        main.removeCallbacks(ticker)
        if (live) {
            previousFrame = null
            lastLiveError = ""
            ignoreAreas = currentIgnoreAreas()
            screen.contentRows = contentRows()
            screen.onFrame = { frame -> onLiveFrame(frame) }
            scheduler.start(nowMs())
            main.post(ticker)
        } else {
            scheduler.stop()
            screen.onFrame = null
            screen.contentRows = null
        }
        overlayShown = false
    }

    /** Rows between the status bar and the navigation bar. */
    private fun contentRows(): Pair<Int, Int>? {
        val (width, height) = capture?.size() ?: return null
        var top = 0
        var bottom = height
        for (area in systemBarAreas(width, height)) {
            if (area[1] == 0) top = maxOf(top, area[3]) else bottom = minOf(bottom, area[1])
        }
        return top to bottom
    }

    private fun setOverlay(next: TranslationOverlay?) {
        overlay?.remove()
        overlay = next
        if (next == null) base = null
        capture?.overlayMask = emptyList()
        next?.let { view ->
            var drawnBoxes = emptyList<PixelRect>()
            view.onDrawn = {
                scheduler.onOverlayDrawn(nowMs())
                // Frames from now on show this drawing (and maybe still the previous
                // one): keep both out of the minor-change check.
                val boxes = view.boxes(margin = 8)
                capture?.overlayMask = drawnBoxes + boxes
                drawnBoxes = boxes
            }
            contentRows()?.let { (top, bottom) ->
                view.clipTop = top.toFloat()
                view.clipBottom = bottom.toFloat()
            }
            view.show()
        }
        overlayShown = next != null
        overlayTracking = next != null
    }

    private fun park(view: TranslationOverlay) {
        if (view.parked) return
        view.parked = true
        overlayShown = false
        capture?.overlayMask = emptyList()
    }

    /**
     * Capture thread: compare with the previous frame.
     *
     * With our overlay on screen, frames mix our drawing with the moving app,
     * so they are not measured: any real change parks (hides) the overlay and
     * keeps it; once the screen is still it is placed by one clean
     * measurement against the base frame. Without an overlay (a full
     * translation running) clean frames are measured so its result can be
     * moved; anything that is not a scroll starts over.
     */
    private fun onLiveFrame(frame: FrameSignature) {
        val previous = previousFrame
        previousFrame = frame
        if (previous == null || !frame.differsFrom(previous, ignoreAreas)) return
        val before = previous.profile
        val now = frame.profile
        if (overlayTracking) {
            // A small in-place change (cursor, scrollbar, spinner) keeps it drawn.
            val minor = overlayShown && before != null && now != null &&
                ScrollEstimator.stillCost(before, now) < ScrollEstimator.MINOR_CHANGE
            main.post { onChangeUnderOverlay(minor) }
            return
        }
        if (translating) {
            val shift = if (before != null && now != null) ScrollEstimator.estimate(before, now, hint = lastDy) else null
            lastDy = if (shift != null && shift.confident) shift.dy else null
            if (shift != null && shift.confident) {
                main.post {
                    scrollSinceCapture += shift.dy
                    lastScrollMs = nowMs()
                    scheduler.onScrolled(lastScrollMs)
                }
                return
            }
        }
        main.post { act(scheduler.onScreenChanged(nowMs())) }
    }

    private fun onChangeUnderOverlay(minor: Boolean) {
        val view = overlay ?: return
        val now = nowMs()
        if (!view.parked && scheduler.inGrace(now)) return // our own drawing appearing
        if (!minor) park(view)
        lastScrollMs = now
        scheduler.onScrolled(now)
    }

    private fun act(action: LiveScheduler.Action) {
        if (action != LiveScheduler.Action.NONE) Log.d(TAG, "live action $action (state ${scheduler.state})")
        when (action) {
            LiveScheduler.Action.NONE -> Unit
            LiveScheduler.Action.HIDE_OVERLAY -> {
                setOverlay(null)
                overlayHiddenAtNanos = System.nanoTime()
            }
            LiveScheduler.Action.TRANSLATE -> translateLive()
            LiveScheduler.Action.TRANSLATE_MORE -> {
                if (!settle()) {
                    // Another page, or scrolled too far to place the old translations.
                    setOverlay(null)
                    overlayHiddenAtNanos = System.nanoTime()
                    scheduler.restart(nowMs())
                    return
                }
                translateMore()
            }
        }
    }

    /**
     * The screen is still again: measure base → now once, place the kept
     * translations and show them at once. False when they cannot be placed.
     */
    private fun settle(): Boolean {
        val view = overlay ?: return false
        val from = base
        val now = previousFrame?.profile
        if (from == null || now == null) {
            Log.d(TAG, "settle: no base (${from != null}) or frame profile (${now != null})")
            return false
        }
        // Whole range, no hint: once per settle, and a hint could stop at a look-alike.
        val shift = ScrollEstimator.estimate(from, now, maxShift = from.rows / 2)
        val total = when {
            shift == null -> {
                Log.d(TAG, "settle: base ${from.top}-${from.bottom}/${from.cols} vs ${now.top}-${now.bottom}/${now.cols}")
                return false
            }
            shift.confident -> shift.dy
            shift.stillCost < ScrollEstimator.MINOR_CHANGE -> 0 // nothing moved since the base
            else -> {
                Log.d(TAG, "settle: cannot place ($shift)")
                return false
            }
        }
        Log.d(TAG, "settle: content moved $total px since base ($shift)")
        movedAtSettle = total - baseTotal
        val dropped = view.place(total, shift::isFixed) { top, bottom, move ->
            ScrollEstimator.regionMatches(from, now, top, bottom, move)
        }
        // Dropped translations leave text anywhere on screen: read it all again.
        readEverything = dropped > 0
        if (view.parked) {
            // The overlay was hidden, so this frame is clean: make it the new
            // base, so the next scroll is measured from here (short distances,
            // always overlapping) instead of from the first translation.
            base = now
            baseTotal = 0
            view.rebase()
            view.parked = false
            overlayShown = true
        } else {
            baseTotal = total
        }
        return true
    }

    /** System bars and the floating button (with a margin for its shadow). */
    private fun currentIgnoreAreas(): List<PixelRect> {
        val screen = capture?.size() ?: return emptyList()
        val areas = systemBarAreas(screen.first, screen.second).map { PixelRect(it[0], it[1], it[2], it[3]) }
        val button = bubble?.screenBounds() ?: return areas
        val margin = (12 * resources.displayMetrics.density).toInt()
        return areas + PixelRect(button.left - margin, button.top - margin, button.right + margin, button.bottom + margin)
    }

    /**
     * After a settle: read only the strip that scrolled in (plus one line of
     * margin for text the edge had cut), skip text already translated, and
     * add the results to the overlay that stays on screen.
     */
    private fun translateMore() {
        translateStartedMs = nowMs()
        val screen = capture ?: return
        val rows = contentRows() ?: return translateLive()
        bubble?.busy = true
        totalAtCapture = baseTotal
        val moved = if (readEverything) 0 else movedAtSettle
        val margin = (64 * resources.displayMetrics.density).toInt()
        val (top, bottom) = rows
        val strip = when {
            // No net scroll (small in-place change) or a long one: read everything
            // not translated yet.
            moved == 0 || kotlin.math.abs(moved) > (bottom - top) * 0.6 -> top to bottom
            moved < 0 -> maxOf(top, bottom + moved - margin) to bottom // scrolled down: new text below
            else -> top to minOf(bottom, top + moved + margin) // scrolled up: new text above
        }
        val skip = overlay?.boxes(margin = 6).orEmpty()
        workerHandler.post {
            val frame = try {
                screen.frameAfter(0, timeoutMs = 100)
            } catch (error: Exception) {
                Log.e(TAG, "live capture failed", error)
                null
            }
            when {
                frame == null -> main.post { liveFailed("") }
                frame.looksBlank() -> main.post { liveFailed("Ứng dụng này chặn chụp màn hình nên không dịch được.") }
                else -> sendToEngine(frame, live = true, extraIgnore = skip, strip = strip)
            }
        }
    }

    private fun translateLive() {
        translateStartedMs = nowMs()
        translating = true
        val screen = capture ?: return
        bubble?.busy = true
        scrollSinceCapture = 0
        lastDy = null
        val rows = contentRows()
        val after = overlayHiddenAtNanos + 40_000_000L
        workerHandler.post {
            val frame = try {
                screen.ensureStarted()
                screen.frameAfter(after, timeoutMs = 400)
            } catch (error: Exception) {
                Log.e(TAG, "live capture failed", error)
                null
            }
            // Nothing of ours is on screen: this frame becomes the base.
            pendingBase = rows?.let { (top, bottom) -> frame?.rowProfile(top, bottom) }
            when {
                frame == null -> main.post { liveFailed("") }
                frame.looksBlank() -> main.post { liveFailed("Ứng dụng này chặn chụp màn hình nên không dịch được.") }
                else -> sendToEngine(frame, live = true)
            }
        }
    }

    @Suppress("UNCHECKED_CAST")
    private fun showLiveResult(result: Any?) {
        translating = false
        bubble?.busy = false
        val map = result as? Map<String, Any?> ?: return liveFailed("Dịch thất bại.")
        Log.d(
            TAG,
            "live result in ${nowMs() - translateStartedMs} ms: found=${map["found"]} " +
                "regions=${(map["regions"] as? List<*>)?.size} error=${map["error"]} ${map["message"] ?: ""}",
        )
        when (map["error"]) {
            "models_missing" -> {
                liveFailed("Chưa có gói dịch offline. Mở VidTrans → Dịch ảnh để tải, hoặc chuyển sang Online.")
                // Retrying on every scroll would only repeat the message.
                live = false
                applyLive()
                return
            }
            "failed" -> return liveFailed(map["message"] as? String ?: "Dịch thất bại.")
        }
        val incremental = scheduler.incremental
        if (!scheduler.onTranslated(nowMs())) return // another page meanwhile
        lastLiveError = ""
        val regions = (map["regions"] as? List<Any?>).orEmpty().map { OverlayRegion.fromMap(it as Map<String, Any?>) }
        val current = overlay
        if (incremental && current != null) {
            // Read where the content stood at capture (base + totalAtCapture).
            if (regions.isNotEmpty()) current.add(regions, totalAtCapture)
            if (current.parked) scheduler.onScrolled(nowMs()) // changed meanwhile: settle again
        } else if (regions.isEmpty() || pendingBase == null) {
            setOverlay(null)
        } else {
            val view = TranslationOverlay(this, emptyList(), null, interactive = false) {}
            view.add(regions, 0)
            setOverlay(view)
            base = pendingBase
            // The content may have scrolled while translating (measured on clean frames).
            baseTotal = scrollSinceCapture
            view.place(baseTotal, isFixed = { false })
            if (nowMs() - lastScrollMs < 200) {
                // Still scrolling: stay hidden, reappear when the screen is still.
                park(view)
                scheduler.onScrolled(nowMs())
            }
        }
        scheduler.onOverlayDrawn(nowMs())
    }

    private fun liveFailed(message: String) {
        translating = false
        Log.w(TAG, "live translation failed: $message")
        bubble?.busy = false
        scheduler.onFailed(nowMs())
        // Say it once, not on every scroll.
        if (message.isNotEmpty() && message != lastLiveError) toast(message)
        lastLiveError = message
    }

    private fun startEngine() {
        val loader = FlutterInjector.instance().flutterLoader()
        loader.startInitialization(applicationContext)
        loader.ensureInitializationComplete(applicationContext, null)
        val flutter = FlutterEngine(applicationContext) // registers every plugin (ML Kit, secure storage…)
        val channel = MethodChannel(flutter.dartExecutor.binaryMessenger, ENGINE_CHANNEL)
        channel.setMethodCallHandler { call: MethodCall, result: MethodChannel.Result ->
            if (call.method == "ready") {
                engineReady = true
                pendingCalls.forEach { it() }
                pendingCalls.clear()
                result.success(null)
            } else {
                result.notImplemented()
            }
        }
        flutter.dartExecutor.executeDartEntrypoint(
            DartExecutor.DartEntrypoint(loader.findAppBundlePath(), ENGINE_LIBRARY, "screenTranslatorMain"),
        )
        engine = flutter
        engineChannel = channel
    }

    private fun whenEngineReady(block: () -> Unit) {
        if (engineReady) block() else pendingCalls.add(block)
    }

    private fun toast(message: String) = main.post {
        Toast.makeText(applicationContext, message, Toast.LENGTH_LONG).show()
    }

    private fun translateScreen() {
        val button = bubble ?: return
        val screen = capture ?: return
        button.busy = true
        button.hideForCapture()
        val hiddenAt = System.nanoTime()
        workerHandler.post {
            val frame = try {
                screen.ensureStarted()
                // Wait for a frame composed after the button disappeared.
                screen.frameAfter(hiddenAt + 40_000_000L)
            } catch (error: Exception) {
                Log.e(TAG, "capture failed", error)
                null
            }
            main.post { button.show() }
            when {
                frame == null -> finish("Chưa chụp được màn hình, hãy thử lại.")
                frame.looksBlank() -> finish("Ứng dụng này chặn chụp màn hình nên không dịch được.")
                else -> sendToEngine(frame, live = false)
            }
        }
    }

    /** Runs on the worker thread: writes the OCR input, then calls Dart. */
    private fun sendToEngine(
        frame: CapturedFrame,
        live: Boolean,
        extraIgnore: List<PixelRect> = emptyList(),
        strip: Pair<Int, Int>? = null,
    ) {
        val file = File(cacheDir, "screen_translator_${System.currentTimeMillis()}.jpg")
        // OCR reads only [strip] when given: less image, faster answer.
        val stripTop = strip?.first?.coerceIn(0, frame.height - 1) ?: 0
        val stripBottom = strip?.second?.coerceIn(stripTop + 1, frame.height) ?: frame.height
        try {
            val bitmap = Bitmap.createBitmap(frame.width, frame.height, Bitmap.Config.ARGB_8888)
            bitmap.copyPixelsFromBuffer(ByteBuffer.wrap(frame.rgba))
            val ocrImage = if (stripTop == 0 && stripBottom == frame.height) bitmap
            else Bitmap.createBitmap(bitmap, 0, stripTop, frame.width, stripBottom - stripTop)
            FileOutputStream(file).use { ocrImage.compress(Bitmap.CompressFormat.JPEG, 92, it) }
            if (ocrImage !== bitmap) ocrImage.recycle()
            bitmap.recycle()
        } catch (error: Exception) {
            Log.e(TAG, "could not write capture", error)
            if (live) main.post { liveFailed("") } else finish("Chưa xử lý được ảnh màn hình, hãy thử lại.")
            return
        }
        val args = mapOf(
            "imagePath" to file.absolutePath,
            "rgba" to frame.rgba,
            "width" to frame.width,
            "height" to frame.height,
            "source" to source,
            "target" to target,
            "mode" to mode,
            // Status and navigation bars: system text (clock, carrier, …)
            // is not part of the app being translated.
            // …nor is the floating button.
            "ignore" to (
                if (live) (ignoreAreas + extraIgnore).map { it.toList() }
                else systemBarAreas(frame.width, frame.height)
                ),
            "ocrOffsetY" to stripTop,
            // Live: skip text cut by the edges of the visible area (or the strip).
            "clip" to if (live) contentRows()?.let { (top, bottom) ->
                listOf(0, maxOf(top, if (stripTop > 0) stripTop else top), frame.width, minOf(bottom, stripBottom))
            } else null,
        )
        fun fail(message: String) = if (live) liveFailed(message) else finish(message)
        main.post {
            // One answer per request: the result, an error, or the timeout.
            var answered = false
            fun answer(block: () -> Unit) {
                if (answered) return
                answered = true
                block()
            }
            main.postDelayed({
                answer {
                    Log.w(TAG, "translation timed out (engine ready: $engineReady)")
                    file.delete()
                    fail(
                        if (engineReady) "Dịch quá lâu, hãy thử lại."
                        else "Không khởi động được bộ dịch. Hãy tắt rồi bật lại nút dịch.",
                    )
                }
            }, TRANSLATE_TIMEOUT_MS)
            whenEngineReady {
                engineChannel?.invokeMethod("translate", args, object : MethodChannel.Result {
                    override fun success(result: Any?) =
                        answer { if (live) showLiveResult(result) else showResult(result) }
                    override fun error(code: String, message: String?, details: Any?) =
                        answer { fail(message ?: "Dịch thất bại, hãy thử lại.") }
                    override fun notImplemented() = answer { fail("Bản cài đặt chưa hỗ trợ dịch màn hình.") }
                })
            }
        }
    }

    private fun systemBarAreas(width: Int, height: Int): List<List<Int>> {
        var top = 0
        var bottom = 0
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val wm = getSystemService(Context.WINDOW_SERVICE) as android.view.WindowManager
            val insets = wm.maximumWindowMetrics.windowInsets.getInsetsIgnoringVisibility(
                android.view.WindowInsets.Type.statusBars() or android.view.WindowInsets.Type.navigationBars(),
            )
            top = insets.top
            bottom = insets.bottom
        } else {
            val res = resources
            fun dimen(name: String): Int {
                val id = res.getIdentifier(name, "dimen", "android")
                return if (id > 0) res.getDimensionPixelSize(id) else 0
            }
            top = dimen("status_bar_height")
            bottom = dimen("navigation_bar_height")
        }
        return buildList {
            if (top > 0) add(listOf(0, 0, width, top))
            if (bottom > 0) add(listOf(0, height - bottom, width, height))
        }
    }

    @Suppress("UNCHECKED_CAST")
    private fun showResult(result: Any?) {
        val map = result as? Map<String, Any?> ?: return finish("Dịch thất bại, hãy thử lại.")
        when (map["error"]) {
            "models_missing" -> return finish(
                "Chưa có gói dịch offline. Mở VidTrans → Dịch ảnh để tải, hoặc chuyển sang chế độ Online.",
            )
            "failed" -> return finish(map["message"] as? String ?: "Dịch thất bại, hãy thử lại.")
        }
        val regions = (map["regions"] as? List<Any?>).orEmpty().map { OverlayRegion.fromMap(it as Map<String, Any?>) }
        if (regions.isEmpty()) {
            val found = (map["found"] as? Number)?.toInt() ?: 0
            return finish(if (found == 0) "Không thấy chữ trên màn hình." else "Không có đoạn nào cần dịch.")
        }
        bubble?.busy = false
        bubble?.hideForCapture() // keep the translation unobstructed
        overlay?.remove()
        overlay = TranslationOverlay(this, regions, "Chạm để đóng bản dịch", interactive = true) { closeOverlay() }
            .also { it.show() }
    }

    private fun closeOverlay() {
        overlay?.remove()
        overlay = null
        bubble?.show()
    }

    private fun finish(message: String) {
        main.post {
            bubble?.busy = false
            bubble?.show()
        }
        toast(message)
    }

    private fun buildNotification(): Notification {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Dịch màn hình", NotificationManager.IMPORTANCE_LOW).apply {
                    description = "Nút dịch nổi của VidTrans đang bật"
                },
            )
        }
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        val stop = PendingIntent.getService(
            this, 1, Intent(this, ScreenTranslatorService::class.java).setAction(ACTION_STOP), flags,
        )
        val open = PendingIntent.getActivity(this, 2, Intent(this, MainActivity::class.java), flags)
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION") Notification.Builder(this)
        }
        return builder
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Nút dịch màn hình đang bật")
            .setContentText("Chạm nút “Dịch” nổi để dịch màn hình hiện tại")
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Tắt", stop).build())
            .build()
    }

    override fun onDestroy() {
        running = false
        liveNow = false
        main.removeCallbacks(ticker)
        scheduler.stop()
        capture?.onFrame = null
        overlay?.remove()
        overlay = null
        bubble?.remove()
        bubble = null
        capture?.release()
        capture = null
        projection?.stop()
        projection = null
        engineChannel?.setMethodCallHandler(null)
        engine?.destroy()
        engine = null
        worker.quitSafely()
        super.onDestroy()
    }
}
