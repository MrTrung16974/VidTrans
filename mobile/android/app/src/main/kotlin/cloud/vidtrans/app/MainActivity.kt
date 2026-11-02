package cloud.vidtrans.app

import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionConfig
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import cloud.vidtrans.app.screentranslator.ScreenTranslatorService
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    companion object {
        private const val SCREEN_CAPTURE_REQUEST = 7302
    }

    private var pendingStart: MethodChannel.Result? = null
    private var pendingSettings: Map<String, String> = emptyMap()

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "vidtrans/screen_translator")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "canDrawOverlays" -> result.success(canDrawOverlays())
                    "openOverlaySettings" -> {
                        startActivity(
                            Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")),
                        )
                        result.success(null)
                    }
                    "isRunning" -> result.success(ScreenTranslatorService.running)
                    "isLive" -> result.success(ScreenTranslatorService.running && ScreenTranslatorService.liveNow)
                    "start" -> requestCapture(settingsOf(call.arguments), result)
                    "updateSettings" -> {
                        if (ScreenTranslatorService.running) {
                            startService(settingsIntent(ScreenTranslatorService.ACTION_SETTINGS, settingsOf(call.arguments)))
                        }
                        result.success(null)
                    }
                    "stop" -> {
                        if (ScreenTranslatorService.running) {
                            startService(
                                Intent(this, ScreenTranslatorService::class.java)
                                    .setAction(ScreenTranslatorService.ACTION_STOP),
                            )
                        }
                        result.success(null)
                    }
                    else -> result.notImplemented()
                }
            }
    }

    private fun canDrawOverlays(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(this)

    @Suppress("UNCHECKED_CAST")
    private fun settingsOf(arguments: Any?): Map<String, String> =
        (arguments as? Map<String, Any?>).orEmpty().mapValues { it.value?.toString() ?: "" }

    private fun settingsIntent(action: String, settings: Map<String, String>): Intent =
        Intent(this, ScreenTranslatorService::class.java)
            .setAction(action)
            .putExtra(ScreenTranslatorService.EXTRA_SOURCE, settings["source"])
            .putExtra(ScreenTranslatorService.EXTRA_TARGET, settings["target"])
            .putExtra(ScreenTranslatorService.EXTRA_MODE, settings["mode"])
            .putExtra(ScreenTranslatorService.EXTRA_LIVE, settings["live"])

    private fun requestCapture(settings: Map<String, String>, result: MethodChannel.Result) {
        if (!canDrawOverlays()) {
            result.error("overlay_permission", "Chưa cấp quyền hiển thị trên ứng dụng khác", null)
            return
        }
        if (ScreenTranslatorService.running) {
            startService(settingsIntent(ScreenTranslatorService.ACTION_SETTINGS, settings))
            result.success(true)
            return
        }
        pendingStart?.success(false)
        pendingStart = result
        pendingSettings = settings
        val manager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        // Android 14+ lets users share a single app; the translator needs the
        // whole screen, so only offer that.
        val consent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            manager.createScreenCaptureIntent(MediaProjectionConfig.createConfigForDefaultDisplay())
        } else {
            manager.createScreenCaptureIntent()
        }
        @Suppress("DEPRECATION")
        startActivityForResult(consent, SCREEN_CAPTURE_REQUEST)
    }

    @Deprecated("FlutterActivity is not a ComponentActivity; Activity Result API is unavailable here.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != SCREEN_CAPTURE_REQUEST) return
        val result = pendingStart ?: return
        pendingStart = null
        if (resultCode != RESULT_OK || data == null) {
            result.success(false)
            return
        }
        val intent = settingsIntent(ScreenTranslatorService.ACTION_START, pendingSettings)
            .putExtra(ScreenTranslatorService.EXTRA_RESULT_CODE, resultCode)
            .putExtra(ScreenTranslatorService.EXTRA_RESULT_DATA, data)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(intent) else startService(intent)
        result.success(true)
    }
}
