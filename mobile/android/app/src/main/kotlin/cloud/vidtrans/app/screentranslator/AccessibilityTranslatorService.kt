package cloud.vidtrans.app.screentranslator

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.content.SharedPreferences
import android.graphics.Bitmap
import android.graphics.Rect
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.util.Log
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.Toast
import androidx.annotation.RequiresApi
import io.flutter.FlutterInjector
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.engine.dart.DartExecutor
import io.flutter.plugin.common.MethodChannel
import kotlin.math.max
import kotlin.math.min

/**
 * Accessibility mode of the screen translator: instead of capturing the
 * screen and reading it with OCR, the text comes from the app's own view
 * tree (exact text, exact positions) and the translation is drawn over it on
 * an accessibility overlay — opaque, yet touches still reach the app. When
 * the app scrolls, the tree reports the new positions at once and cached
 * translations are redrawn there: no OCR, no guessing how far it moved.
 *
 * Text the tree does not have (pictures, games, video) cannot be read here;
 * the capture mode (ScreenTranslatorService) remains for those.
 *
 * Enabled by the user in Settings › Accessibility; the app turns the
 * translator on and off and sets the languages through [PREFS].
 */
class AccessibilityTranslatorService : AccessibilityService() {
    companion object {
        private const val TAG = "ScreenTranslatorA11y"
        const val PREFS = "screen_translator_a11y"
        const val KEY_ENABLED = "enabled"
        const val KEY_SOURCE = "source"
        const val KEY_TARGET = "target"
        const val KEY_MODE = "mode"
        private const val ENGINE_CHANNEL = "vidtrans/screen_translator_engine"
        private const val ENGINE_LIBRARY =
            "package:vidtrans_mobile/features/screen_translator/screen_translator_engine.dart"
        private const val BATCH = 40
        private const val RETRY_MS = 15_000L
        private const val MAX_NODES = 3000
        private const val CALM_MS = 150L
        private const val STABLE_MS = 100L
        private val SKIP_PACKAGES = setOf("com.android.systemui")

        /** The user enabled the service in the system settings and it is bound. */
        @Volatile
        var connected = false
            private set
    }

    private val main = Handler(Looper.getMainLooper())
    private lateinit var prefs: SharedPreferences
    private var bubble: FloatingBubble? = null
    private var overlay: TranslationOverlay? = null
    private var engine: FlutterEngine? = null
    private var engineChannel: MethodChannel? = null
    private var engineReady = false
    private val pendingCalls = ArrayList<() -> Unit>()

    private var source = "auto"
    private var target = "vi"
    private var mode = "online"
    private var enabled = false
    private var paused = false

    /** text → translation; null = needs no translation (already in the target language…). */
    private val translations = HashMap<String, String?>()
    private val colors = HashMap<String, Pair<Int, Int>>()
    private val failedAt = HashMap<String, Long>()
    private val inFlight = HashSet<String>()
    private var translating = false
    private var screenshotting = false
    private var lastScreenshotMs = 0L

    /** Last accessibility event, and since when colours wait for the screen to calm down. */
    private var lastEventMs = 0L
    private var colorsWaitingSinceMs = 0L

    /**
     * After a scroll the layer is hidden until the tree settles: apps (Chrome
     * above all) update their accessibility tree a little after drawing, so
     * positions read mid-scroll are stale. Shown again once two reads
     * [STABLE_MS] apart agree.
     */
    private var settling = false
    private var lastRead: List<ScreenText>? = null
    private var lastError = ""
    private var items: List<ScreenText> = emptyList()
    private val fits = HashMap<String, Float>()

    /**
     * Where the characters really are inside a node, as insets from its
     * bounds (left, top, right, bottom), by "text|width|height". A web page
     * reports a whole row as the node; the text (a chat bubble) is a part.
     */
    private val insets = HashMap<String, IntArray?>()
    private val insetTries = HashMap<String, Int>()

    private val refreshRunnable = Runnable { refresh() }
    private var refreshDueMs = 0L
    private var firstPendingChangeMs = 0L

    private val prefsListener = SharedPreferences.OnSharedPreferenceChangeListener { _, _ -> applyPrefs() }

    override fun onServiceConnected() {
        super.onServiceConnected()
        connected = true
        prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        prefs.registerOnSharedPreferenceChangeListener(prefsListener)
        applyPrefs()
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        shutdown()
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        shutdown()
        super.onDestroy()
    }

    override fun onInterrupt() = Unit

    private fun shutdown() {
        connected = false
        if (::prefs.isInitialized) prefs.unregisterOnSharedPreferenceChangeListener(prefsListener)
        hideUi()
        engine?.destroy()
        engine = null
        engineChannel = null
        engineReady = false
        pendingCalls.clear()
    }

    private fun applyPrefs() {
        val newSource = prefs.getString(KEY_SOURCE, "auto") ?: "auto"
        val newTarget = prefs.getString(KEY_TARGET, "vi") ?: "vi"
        val newMode = prefs.getString(KEY_MODE, "online") ?: "online"
        if (newSource != source || newTarget != target || newMode != mode) {
            translations.clear()
            failedAt.clear()
            lastError = ""
        }
        source = newSource
        target = newTarget
        mode = newMode
        enabled = prefs.getBoolean(KEY_ENABLED, false)
        if (enabled) showUi() else hideUi()
    }

    private fun showUi() {
        if (engine == null) startEngine()
        // The translation layer first: the button is added after it and stays on top.
        if (overlay == null) {
            overlay = TranslationOverlay(this, emptyList(), null, interactive = false, trusted = true, onClose = {})
                .also { it.show() }
        }
        if (bubble == null) {
            bubble = FloatingBubble(
                this, onTap = ::togglePaused, onLongPress = ::retranslate, trusted = true,
                // Dropped on the bin: translating off (the app shows it as off too).
                onDismiss = { main.post { prefs.edit().putBoolean(KEY_ENABLED, false).apply() } },
            )
                .also { it.live = !paused }
        }
        bubble?.show()
        scheduleRefresh(0)
    }

    private fun hideUi() {
        main.removeCallbacks(refreshRunnable)
        bubble?.remove()
        bubble = null
        overlay?.remove()
        overlay = null
        items = emptyList()
    }

    /** Tap on the button: stop drawing translations (the button stays) or start again. */
    private fun togglePaused() {
        paused = !paused
        bubble?.live = !paused
        settling = false
        overlay?.parked = false
        if (paused) overlay?.replaceAll(emptyList()) else scheduleRefresh(0)
    }

    /** Long press: forget what this screen was translated to and translate it again. */
    private fun retranslate() {
        for (item in items) {
            translations.remove(item.text)
            colors.remove(colorKey(item))
            failedAt.remove(item.text)
        }
        lastError = ""
        paused = false
        bubble?.live = true
        overlay?.replaceAll(emptyList())
        scheduleRefresh(0)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null || !enabled || paused || overlay == null) return
        lastEventMs = SystemClock.uptimeMillis()
        when (event.eventType) {
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> {
                // Another screen: the old boxes are wrong there, take them off at once.
                if (event.packageName?.toString() != packageName) overlay?.replaceAll(emptyList())
                scheduleRefresh(250)
            }
            // Scrolling: positions changed, the texts mostly did not — redraw
            // from the cache as soon as the tree has the new positions.
            AccessibilityEvent.TYPE_VIEW_SCROLLED -> {
                if (!settling) {
                    settling = true
                    lastRead = null
                    overlay?.parked = true
                }
                scheduleRefresh(CALM_MS)
            }
            else -> scheduleRefresh(200)
        }
    }

    /**
     * Debounced: a burst of content changes (a list being filled) ends in one
     * read, but never more than 600 ms after the first change.
     */
    private fun scheduleRefresh(delayMs: Long) {
        val now = SystemClock.uptimeMillis()
        if (firstPendingChangeMs == 0L) firstPendingChangeMs = now
        val due = min(now + delayMs, firstPendingChangeMs + 600)
        if (refreshDueMs in (now + 1)..due) return // an earlier refresh is already coming
        refreshDueMs = due
        main.removeCallbacks(refreshRunnable)
        main.postAtTime(refreshRunnable, due)
    }

    private fun refresh() {
        refreshDueMs = 0L
        firstPendingChangeMs = 0L
        val layer = overlay ?: return
        if (!enabled || paused) return
        val root = rootInActiveWindow
        val pkg = root?.packageName?.toString()
        if (root == null || pkg == packageName || pkg in SKIP_PACKAGES) {
            items = emptyList()
            layer.replaceAll(emptyList())
            return
        }
        val read = readTexts(root)
        if (settling) {
            val quietFor = SystemClock.uptimeMillis() - lastEventMs
            if (quietFor < CALM_MS) {
                scheduleRefresh(CALM_MS - quietFor)
                return
            }
            if (read != lastRead) {
                lastRead = read
                scheduleRefresh(STABLE_MS)
                return
            }
            settling = false
            lastRead = null
            // Read once more now that it is still: characters get measured.
            items = readTexts(root)
            draw()
            layer.parked = false
        } else {
            items = read
            draw()
        }
        requestColors()
        requestTranslations()
    }

    private fun readTexts(root: AccessibilityNodeInfo? = rootInActiveWindow): List<ScreenText> {
        val found = ArrayList<ScreenText>()
        if (root != null) collect(root, found, intArrayOf(0))
        return A11yText.select(found, minSide = (resources.displayMetrics.density * 6).toInt())
    }

    private fun collect(node: AccessibilityNodeInfo, out: MutableList<ScreenText>, visited: IntArray) {
        if (visited[0]++ > MAX_NODES || !node.isVisibleToUser) return
        val text = node.text?.toString()?.trim()
        if (!text.isNullOrEmpty() && !node.isEditable && !node.isPassword) {
            val r = Rect()
            node.getBoundsInScreen(r)
            val screen = resources.displayMetrics
            val inset = insetsFor(node, text, r)
            if (inset != null) {
                r.set(r.left + inset[0], r.top + inset[1], r.right - inset[2], r.bottom - inset[3])
            }
            if (r.intersect(0, 0, screen.widthPixels, screen.heightPixels)) {
                out.add(ScreenText(text, PixelRect(r.left, r.top, r.right, r.bottom)))
            }
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            collect(child, out, visited)
        }
    }

    /**
     * Cached insets of the characters in a node. Measured only on a still
     * screen (bounds and characters are two queries: mid-scroll they disagree),
     * and an answer of "the whole node" is asked again a few times — a web
     * page reports that until its text is laid out.
     */
    private fun insetsFor(node: AccessibilityNodeInfo, text: String, r: Rect): IntArray? {
        val key = "$text|${r.width()}|${r.height()}"
        if (key in insets) return insets[key]
        if (settling || SystemClock.uptimeMillis() - lastEventMs < CALM_MS) return null
        val inset = textInsets(node, text, r)
        val tries = (insetTries[key] ?: 0) + 1
        insetTries[key] = tries
        if (inset != null && inset.any { it > 0 } || tries >= 3) insets[key] = inset
        return inset
    }

    /** The characters' bounds inside [bounds], or null when unknown. */
    private fun textInsets(node: AccessibilityNodeInfo, text: String, bounds: Rect): IntArray? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return null
        // Platform views use the android.view… key, Chrome the AndroidX
        // (android.core.view…) spelling of the same request.
        val key = node.availableExtraData.firstOrNull { it.endsWith(".DATA_TEXT_CHARACTER_LOCATION_KEY") } ?: return null
        val length = min(node.text?.length ?: 0, AccessibilityNodeInfo.EXTRA_DATA_TEXT_CHARACTER_LOCATION_ARG_MAX_LENGTH)
        if (length == 0) return null
        val args = android.os.Bundle().apply {
            putInt(AccessibilityNodeInfo.EXTRA_DATA_TEXT_CHARACTER_LOCATION_ARG_START_INDEX, 0)
            putInt(AccessibilityNodeInfo.EXTRA_DATA_TEXT_CHARACTER_LOCATION_ARG_LENGTH, length)
            putInt("android.core.view.accessibility.extra.DATA_TEXT_CHARACTER_LOCATION_ARG_START_INDEX", 0)
            putInt("android.core.view.accessibility.extra.DATA_TEXT_CHARACTER_LOCATION_ARG_LENGTH", length)
        }
        return try {
            if (!node.refreshWithExtraData(key, args)) return null
            @Suppress("DEPRECATION")
            val boxes = node.extras.getParcelableArray(key) ?: return null
            val union = android.graphics.RectF()
            for (box in boxes) {
                val b = box as? android.graphics.RectF ?: continue
                if (b.width() > 0f && b.height() > 0f) union.union(b)
            }
            if (union.isEmpty) return null
            // Union is in screen pixels, read now: [bounds] was read just before.
            val inset = intArrayOf(
                (union.left - bounds.left).toInt(), (union.top - bounds.top).toInt(),
                (bounds.right - union.right).toInt(), (bounds.bottom - union.bottom).toInt(),
            )
            if (inset.any { it < 0 } || inset[0] + inset[2] >= bounds.width() || inset[1] + inset[3] >= bounds.height()) {
                null
            } else {
                inset
            }
        } catch (error: RuntimeException) {
            Log.w(TAG, "character bounds unavailable", error)
            null
        }
    }

    private fun draw() {
        val layer = overlay ?: return
        val regions = items.mapNotNull { item ->
            val translated = translations[item.text] ?: return@mapNotNull null
            val (bg, fg) = colors[colorKey(item)] ?: return@mapNotNull null
            val b = item.bounds
            val box = android.graphics.RectF(b.left.toFloat(), b.top.toFloat(), b.right.toFloat(), b.bottom.toFloat())
            OverlayRegion(translated, box, listOf(box), fitSize(translated, b), bg, fg)
        }
        layer.replaceAll(regions)
    }

    /** Largest font (up to ~24 sp) at which [text] fits in [box]. */
    private fun fitSize(text: String, box: PixelRect): Float {
        val w = box.right - box.left
        val h = box.bottom - box.top
        return fits.getOrPut("$w×$h:$text") {
            val sp = resources.displayMetrics.scaledDensity
            var size = min(h / 1.18f, 24 * sp)
            val paint = TextPaint(TextPaint.ANTI_ALIAS_FLAG)
            while (size > 7 * sp) {
                paint.textSize = size
                val layout = StaticLayout.Builder.obtain(text, 0, text.length, paint, max(1, w))
                    .setAlignment(Layout.Alignment.ALIGN_NORMAL)
                    .setIncludePad(false)
                    .build()
                if (layout.lineCount * size * 1.18f <= h + 1) break
                size *= 0.9f
            }
            size
        }
    }

    /**
     * Colours come from a screenshot (Android 11+) taken before the box is
     * drawn, so they are the app's own; earlier versions draw dark text on
     * light grey.
     */
    private fun requestColors() {
        val missing = items.filter { colorKey(it) !in colors && translations[it.text] != null }
        if (missing.isEmpty() || screenshotting || settling) return
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            for (item in missing) colors[colorKey(item)] = A11yText.colors(IntArray(0))
            draw()
            return
        }
        val now = SystemClock.uptimeMillis()
        // Sample only a still screen: mid-scroll or while a dialog fades the
        // boxes read a moment ago are no longer where the screenshot has them.
        // A screen that never calms down (a ticking clock) is sampled anyway.
        if (colorsWaitingSinceMs == 0L) colorsWaitingSinceMs = now
        val calm = lastEventMs + 300 - now
        if (calm > 0 && now - colorsWaitingSinceMs < 1500) {
            scheduleRefresh(calm)
            return
        }
        // At most ~3 screenshots a second are allowed.
        val wait = lastScreenshotMs + 350 - now
        if (wait > 0) {
            scheduleRefresh(wait)
            return
        }
        colorsWaitingSinceMs = 0L
        screenshotting = true
        lastScreenshotMs = SystemClock.uptimeMillis()
        takeScreenshotFor(missing)
    }

    @RequiresApi(Build.VERSION_CODES.R)
    private fun takeScreenshotFor(missing: List<ScreenText>) {
        val requestedAt = SystemClock.uptimeMillis()
        takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
            override fun onSuccess(result: ScreenshotResult) {
                screenshotting = false
                val buffer = result.hardwareBuffer
                // Anything happened since the request (a new scroll): the tree
                // may lag the screen, so the boxes cannot be trusted. Retry when still.
                if (lastEventMs > requestedAt || settling) {
                    buffer.close()
                    scheduleRefresh(CALM_MS)
                    return
                }
                val bitmap = try {
                    Bitmap.wrapHardwareBuffer(buffer, result.colorSpace)?.copy(Bitmap.Config.ARGB_8888, false)
                } finally {
                    buffer.close()
                }
                // Keep only boxes still where they were read: anything that moved
                // meanwhile is sampled again on the next still screen.
                val now = readTexts().toSet()
                val button = bubble?.screenBounds()
                for (item in missing) {
                    if (item !in now) continue
                    colors[colorKey(item)] = A11yText.colors(bitmap?.let { sample(it, item.bounds, button) } ?: IntArray(0))
                }
                bitmap?.recycle()
                refresh()
            }

            override fun onFailure(errorCode: Int) {
                screenshotting = false
                Log.w(TAG, "screenshot failed: $errorCode")
                for (item in missing) colors.getOrPut(colorKey(item)) { A11yText.colors(IntArray(0)) }
                draw()
            }
        })
    }

    /** The same text in a box of another size (another bubble, a heading) may have other colours. */
    private fun colorKey(item: ScreenText) =
        "${item.text}|${item.bounds.right - item.bounds.left}|${item.bounds.bottom - item.bounds.top}"

    /** A grid of pixels inside [box], without the ones under our button. */
    private fun sample(bitmap: Bitmap, box: PixelRect, skip: PixelRect?): IntArray {
        val left = box.left.coerceIn(0, bitmap.width - 1)
        val right = box.right.coerceIn(left + 1, bitmap.width)
        val top = box.top.coerceIn(0, bitmap.height - 1)
        val bottom = box.bottom.coerceIn(top + 1, bitmap.height)
        val cols = min(48, right - left)
        val rows = min(16, bottom - top)
        val out = IntArray(cols * rows)
        var n = 0
        for (r in 0 until rows) {
            val y = top + (r * 2 + 1) * (bottom - top) / (rows * 2)
            for (c in 0 until cols) {
                val x = left + (c * 2 + 1) * (right - left) / (cols * 2)
                if (skip?.contains(x, y) == true) continue
                out[n++] = bitmap.getPixel(x, y)
            }
        }
        return out.copyOf(n)
    }

    private fun requestTranslations() {
        if (translating) return
        val now = SystemClock.uptimeMillis()
        val todo = items.asSequence().map { it.text }
            .filter { it !in translations && it !in inFlight && (failedAt[it]?.let { t -> now - t > RETRY_MS } ?: true) }
            .distinct().take(BATCH).toList()
        if (todo.isEmpty()) return
        translating = true
        inFlight.addAll(todo)
        bubble?.busy = true
        val args = mapOf("texts" to todo, "source" to source, "target" to target, "mode" to mode)
        whenEngineReady {
            engineChannel?.invokeMethod("translateTexts", args, object : MethodChannel.Result {
                override fun success(result: Any?) = done(todo, result as? Map<*, *>)
                override fun error(code: String, message: String?, details: Any?) =
                    done(todo, mapOf("error" to code, "message" to (message ?: "")))
                override fun notImplemented() = done(todo, mapOf("error" to "not_implemented", "message" to ""))
            })
        }
    }

    private fun done(texts: List<String>, reply: Map<*, *>?) {
        translating = false
        inFlight.removeAll(texts.toSet())
        bubble?.busy = false
        val list = reply?.get("translations") as? List<*>
        if (list != null && list.size == texts.size) {
            texts.forEachIndexed { i, text -> translations[text] = list[i] as? String }
            lastError = ""
        } else {
            val now = SystemClock.uptimeMillis()
            texts.forEach { failedAt[it] = now }
            val message = when (reply?.get("error")) {
                "models_missing" -> "Chưa tải gói ngôn ngữ ngoại tuyến: ${(reply["languages"] as? List<*>)?.joinToString()}"
                else -> (reply?.get("message") as? String).orEmpty().ifEmpty { "Dịch không thành công, thử lại sau." }
            }
            Log.w(TAG, "translate failed: $reply")
            if (message != lastError) Toast.makeText(applicationContext, message, Toast.LENGTH_LONG).show()
            lastError = message
        }
        // Positions may have changed while waiting; more texts may be left.
        refresh()
    }

    private fun startEngine() {
        val loader = FlutterInjector.instance().flutterLoader()
        loader.startInitialization(applicationContext)
        loader.ensureInitializationComplete(applicationContext, null)
        val flutter = FlutterEngine(applicationContext)
        val channel = MethodChannel(flutter.dartExecutor.binaryMessenger, ENGINE_CHANNEL)
        channel.setMethodCallHandler { call, result ->
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
}
