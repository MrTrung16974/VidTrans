package cloud.vidtrans.app.screentranslator

import kotlin.math.abs

/** A piece of text the app on screen shows, read from its view tree. */
data class ScreenText(val text: String, val bounds: PixelRect)

/** Accessibility-mode helpers kept free of Android classes for JVM tests. */
object A11yText {
    /** More boxes than this on one screen are a list of thousands of items: draw the first ones. */
    const val MAX_TEXTS = 160

    /** Worth sending to the translator: has letters and is not just a number, time or symbol. */
    fun isTranslatable(text: String): Boolean = text.any { it.isLetter() }

    /**
     * Texts to draw, in tree order, without the ones fully covered by an
     * earlier one with the same text (a button and its label both report it)
     * and without boxes too small to hold a readable translation.
     */
    fun select(found: List<ScreenText>, minSide: Int): List<ScreenText> {
        val kept = ArrayList<ScreenText>()
        for (item in found) {
            val b = item.bounds
            if (b.right - b.left < minSide || b.bottom - b.top < minSide) continue
            if (!isTranslatable(item.text)) continue
            if (kept.any { it.text == item.text && it.bounds.encloses(b) }) continue
            kept.add(item)
            if (kept.size >= MAX_TEXTS) break
        }
        return kept
    }

    private fun PixelRect.encloses(o: PixelRect) =
        left <= o.left && top <= o.top && right >= o.right && bottom >= o.bottom

    /**
     * Background and text colour of a box from pixel samples taken inside it:
     * the background is the most common colour (text strokes are thin), the
     * text colour the most common one clearly different from it. Without one
     * (an empty-looking box) the text colour is black or white, whichever
     * reads better.
     */
    fun colors(samples: IntArray): Pair<Int, Int> {
        if (samples.isEmpty()) return 0xFFF5F5F5.toInt() to 0xFF111111.toInt()
        val counts = HashMap<Int, Int>()
        for (c in samples) counts.merge(quantize(c), 1, Int::plus)
        val bgKey = counts.maxBy { it.value }.key
        val bg = average(samples.filter { quantize(it) == bgKey })
        val fgKey = counts.entries
            .filter { contrast(it.key, bgKey) >= 90 }
            .maxByOrNull { it.value }
            ?.takeIf { it.value * 50 >= samples.size } // ≥2%: real strokes, not noise
            ?.key
        val fg = if (fgKey != null) average(samples.filter { quantize(it) == fgKey })
        else if (luma(bg) > 140) 0xFF111111.toInt() else 0xFFFFFFFF.toInt()
        return bg to fg
    }

    private fun quantize(c: Int): Int = c and 0xE0E0E0 // 8 levels per channel

    private fun luma(c: Int): Int =
        (((c shr 16) and 0xFF) * 299 + ((c shr 8) and 0xFF) * 587 + (c and 0xFF) * 114) / 1000

    private fun contrast(a: Int, b: Int) = abs(luma(a) - luma(b)) +
        (abs(((a shr 16) and 0xFF) - ((b shr 16) and 0xFF)) +
            abs(((a shr 8) and 0xFF) - ((b shr 8) and 0xFF)) +
            abs((a and 0xFF) - (b and 0xFF))) / 6

    private fun average(colors: List<Int>): Int {
        var r = 0L
        var g = 0L
        var b = 0L
        for (c in colors) {
            r += (c shr 16) and 0xFF
            g += (c shr 8) and 0xFF
            b += c and 0xFF
        }
        val n = colors.size.coerceAtLeast(1)
        return (0xFF shl 24) or ((r / n).toInt() shl 16) or ((g / n).toInt() shl 8) or (b / n).toInt()
    }
}
