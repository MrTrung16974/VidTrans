package cloud.vidtrans.app.screentranslator

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class A11yTextTest {
    private fun item(text: String, l: Int, t: Int, r: Int, b: Int) = ScreenText(text, PixelRect(l, t, r, b))

    @Test
    fun translatableNeedsLetters() {
        assertTrue(A11yText.isTranslatable("你好"))
        assertTrue(A11yText.isTranslatable("Send"))
        assertFalse(A11yText.isTranslatable("12:45"))
        assertFalse(A11yText.isTranslatable("+84 · 99%"))
    }

    @Test
    fun selectDropsTinyNumbersAndNestedDuplicates() {
        val kept = A11yText.select(
            listOf(
                item("发送", 0, 0, 200, 80), // button
                item("发送", 20, 10, 180, 70), // its label, same text inside
                item("发送", 300, 0, 500, 80), // same text elsewhere: kept
                item("12:45", 0, 100, 200, 160),
                item("小", 0, 200, 4, 204), // too small
            ),
            minSide = 10,
        )
        assertEquals(listOf(PixelRect(0, 0, 200, 80), PixelRect(300, 0, 500, 80)), kept.map { it.bounds })
    }

    @Test
    fun selectCapsTheCount() {
        val many = (0 until 500).map { item("字$it", 0, it * 20, 100, it * 20 + 18) }
        assertEquals(A11yText.MAX_TEXTS, A11yText.select(many, minSide = 5).size)
    }

    @Test
    fun colorsTakeBackgroundAndStrokeColour() {
        val white = 0xFFFFFFFF.toInt()
        val blue = 0xFF1E40AF.toInt()
        val samples = IntArray(200) { if (it % 5 == 0) blue else white }
        val (bg, fg) = A11yText.colors(samples)
        assertEquals(white, bg)
        assertEquals(blue, fg)
    }

    @Test
    fun colorsFallBackToContrastingText() {
        val dark = 0xFF101820.toInt()
        val (bg, fg) = A11yText.colors(IntArray(100) { dark })
        assertEquals(dark, bg)
        assertEquals(0xFFFFFFFF.toInt(), fg)
    }
}
