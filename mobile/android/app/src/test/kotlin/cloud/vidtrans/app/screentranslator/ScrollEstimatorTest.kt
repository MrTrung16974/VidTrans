package cloud.vidtrans.app.screentranslator

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

class ScrollEstimatorTest {
    private val cols = PROFILE_COLS
    private val rows = 1800

    /** A long chat-like page: text lines (noisy) and gaps (flat); the screen shows rows [offset, offset + rows). */
    private val page: IntArray = Random(7).let { random ->
        IntArray((rows * 3) * cols) { i ->
            val y = i / cols
            if (y % 40 < 22 && (y / 40) % 3 != 2) random.nextInt(20, 230) else 237
        }
    }

    /**
     * The screen at scroll [offset]; [fixedTop]/[fixedBottom] rows do not scroll;
     * samples inside [overlay] (screen rows) show a static translation (masked
     * when [masked], else a flat colour like an 80% overlay would mostly show).
     */
    private fun screen(
        offset: Int,
        fixedTop: Int = 0,
        fixedBottom: Int = 0,
        overlay: IntRange? = null,
        masked: Boolean = true,
    ): RowProfile {
        val values = IntArray(rows * cols)
        for (y in 0 until rows) {
            for (k in 0 until cols) {
                values[y * cols + k] = when {
                    overlay != null && y in overlay && k < cols * 3 / 4 -> if (masked) RowProfile.MASKED else 250
                    y < fixedTop -> 90 + (y % 7) * 9 // toolbar: does not scroll
                    y >= rows - fixedBottom -> 120 + (y % 5) * 11
                    else -> page[(y + offset) * cols + k]
                }
            }
        }
        return RowProfile(100, 100 + rows, cols, values)
    }

    @Test
    fun `finds the scroll distance in both directions`() {
        val down = ScrollEstimator.estimate(screen(200), screen(237))!!
        assertTrue(down.toString(), down.confident)
        assertEquals(-37, down.dy) // content moved up by 37 px
        val up = ScrollEstimator.estimate(screen(237), screen(200))!!
        assertTrue(up.confident)
        assertEquals(37, up.dy)
    }

    @Test
    fun `large flings are found too`() {
        val shift = ScrollEstimator.estimate(screen(100), screen(650))!!
        assertTrue(shift.toString(), shift.confident)
        assertEquals(-550, shift.dy)
    }

    @Test
    fun `a good hint is used and a wrong one does not mislead`() {
        assertEquals(-60, ScrollEstimator.estimate(screen(100), screen(160), hint = -55)!!.dy)
        val wrong = ScrollEstimator.estimate(screen(100), screen(160), hint = 300)!!
        assertTrue(wrong.confident)
        assertEquals(-60, wrong.dy)
    }

    @Test
    fun `our own overlay standing still over moving content does not hide the scroll`() {
        // The translation covers rows 300..1100 and has not moved yet; masked, the rest decides.
        val shift = ScrollEstimator.estimate(screen(200, overlay = 300..1100), screen(240, overlay = 300..1100))!!
        assertTrue(shift.toString(), shift.confident)
        assertEquals(-40, shift.dy)
    }

    @Test
    fun `toolbars that stay put are reported as fixed`() {
        val shift = ScrollEstimator.estimate(screen(300, fixedTop = 150, fixedBottom = 120), screen(345, 150, 120))!!
        assertTrue(shift.confident)
        assertEquals(-45, shift.dy)
        assertTrue(shift.isFixed(100f + 40f))
        assertFalse(shift.isFixed(100f + 900f))
        assertTrue(shift.isFixed(100f + rows - 40f))
    }

    @Test
    fun `a different screen is not mistaken for a scroll and is not a minor change`() {
        val other = RowProfile(100, 100 + rows, cols, IntArray(rows * cols) { Random(it).nextInt(0, 255) })
        val shift = ScrollEstimator.estimate(screen(200), other)!!
        assertFalse(shift.confident)
        assertTrue(shift.stillCost > ScrollEstimator.MINOR_CHANGE)
    }

    @Test
    fun `a blinking cursor is a minor change`() {
        val blinking = screen(200).let { base ->
            RowProfile(base.top, base.bottom, cols, base.values.copyOf().also { v -> for (y in 600 until 640) v[y * cols] = 0 })
        }
        val shift = ScrollEstimator.estimate(screen(200), blinking)!!
        assertFalse(shift.confident)
        assertTrue(shift.toString(), shift.stillCost < ScrollEstimator.MINOR_CHANGE)
    }

    @Test
    fun `one frame is estimated within a few milliseconds`() {
        val prev = screen(100, overlay = 300..900)
        val cur = screen(160, overlay = 300..900)
        repeat(3) { ScrollEstimator.estimate(prev, cur, hint = -50) } // warm up the JIT
        var start = System.nanoTime()
        repeat(20) { ScrollEstimator.estimate(prev, cur, hint = -50) }
        val withHint = (System.nanoTime() - start) / 20 / 1_000_000.0
        start = System.nanoTime()
        repeat(5) { ScrollEstimator.estimate(prev, cur) }
        val full = (System.nanoTime() - start) / 5 / 1_000_000.0
        println("ScrollEstimator: ${"%.2f".format(withHint)} ms with hint, ${"%.2f".format(full)} ms full search")
        assertTrue("too slow: $withHint ms", withHint < 15)
    }

    @Test
    fun `a translation is kept only where its text still is`() {
        val base = screen(200)
        val now = screen(260) // content moved up 60 px
        assertTrue(ScrollEstimator.regionMatches(base, now, 100f + 500f, 100f + 560f, -60))
        assertFalse(ScrollEstimator.regionMatches(base, now, 100f + 500f, 100f + 560f, -180)) // look-alike spot
        assertFalse(ScrollEstimator.regionMatches(base, now, 100f + 500f, 100f + 560f, 0)) // did not move with it
    }

    @Test
    fun `an unchanged screen is not a scroll`() {
        assertFalse(ScrollEstimator.estimate(screen(200), screen(200))!!.confident)
    }

    @Test
    fun `rotation or resize cannot be compared`() {
        assertNull(ScrollEstimator.estimate(screen(0), RowProfile(0, rows, cols, IntArray(rows * cols))))
    }
}
