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
    private var mode = "offline"

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
        source = intent.getStringExtra(EXTRA_SOURCE) ?: source
        target = intent.getStringExtra(EXTRA_TARGET) ?: target
        mode = intent.getStringExtra(EXTRA_MODE) ?: mode
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
        bubble = FloatingBubble(this) { translateScreen() }.also { button ->
            button.show()
            button.post {
                val at = IntArray(2)
                button.getLocationOnScreen(at)
                // Read by scripts/test_screen_translator.sh to tap the button.
                Log.i(TAG, "bubble at ${at[0]},${at[1]},${button.width},${button.height}")
            }
        }
        running = true
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
                else -> sendToEngine(frame)
            }
        }
    }

    /** Runs on the worker thread: writes the OCR input, then calls Dart. */
    private fun sendToEngine(frame: CapturedFrame) {
        val file = File(cacheDir, "screen_translator_${System.currentTimeMillis()}.jpg")
        try {
            val bitmap = Bitmap.createBitmap(frame.width, frame.height, Bitmap.Config.ARGB_8888)
            bitmap.copyPixelsFromBuffer(ByteBuffer.wrap(frame.rgba))
            FileOutputStream(file).use { bitmap.compress(Bitmap.CompressFormat.JPEG, 92, it) }
            bitmap.recycle()
        } catch (error: Exception) {
            Log.e(TAG, "could not write capture", error)
            finish("Chưa xử lý được ảnh màn hình, hãy thử lại.")
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
            "ignore" to systemBarAreas(frame.width, frame.height),
        )
        main.post {
            // One answer per tap: the result, an error, or the timeout.
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
                    finish(
                        if (engineReady) "Dịch quá lâu, hãy thử lại."
                        else "Không khởi động được bộ dịch. Hãy tắt rồi bật lại nút dịch.",
                    )
                }
            }, TRANSLATE_TIMEOUT_MS)
            whenEngineReady {
                engineChannel?.invokeMethod("translate", args, object : MethodChannel.Result {
                    override fun success(result: Any?) = answer { showResult(result) }
                    override fun error(code: String, message: String?, details: Any?) =
                        answer { finish(message ?: "Dịch thất bại, hãy thử lại.") }
                    override fun notImplemented() = answer { finish("Bản cài đặt chưa hỗ trợ dịch màn hình.") }
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
        overlay = TranslationOverlay(this, regions, "Chạm để đóng bản dịch") { closeOverlay() }.also { it.show() }
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
