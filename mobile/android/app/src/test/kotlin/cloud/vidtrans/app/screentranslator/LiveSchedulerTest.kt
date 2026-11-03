package cloud.vidtrans.app.screentranslator

import cloud.vidtrans.app.screentranslator.LiveScheduler.Action
import cloud.vidtrans.app.screentranslator.LiveScheduler.State
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LiveSchedulerTest {
    private fun signature(vararg changed: Int, value: Int = 200): FrameSignature {
        val luma = ByteArray(100) { 30 }
        changed.forEach { luma[it] = value.toByte() }
        return FrameSignature(1000, 1000, 10, 10, luma, 0)
    }

    @Test
    fun `small changes like a blinking cursor do not count`() {
        assertFalse(signature().differsFrom(signature(5), emptyList()))
        assertTrue(signature().differsFrom(signature(1, 2, 3, 4, 5), emptyList()))
    }

    @Test
    fun `changes inside ignored areas do not count`() {
        // samples 0..9 are the top row (y = 50): the status bar.
        val statusBar = listOf(PixelRect(0, 0, 1000, 100))
        assertFalse(signature().differsFrom(signature(0, 1, 2, 3, 4, 5, 6), statusBar))
        assertTrue(signature().differsFrom(signature(10, 11, 12, 13, 14), statusBar))
    }

    @Test
    fun `a rotated or resized screen always counts as changed`() {
        val rotated = FrameSignature(1000, 500, 10, 10, ByteArray(100) { 30 }, 0)
        assertTrue(signature().differsFrom(rotated, emptyList()))
    }

    @Test
    fun `translates once the screen is still, then shows the result`() {
        val live = LiveScheduler(settleMs = 300, minGapMs = 0, graceMs = 200)
        live.start(0)
        assertEquals(Action.NONE, live.onTick(100))
        assertEquals(Action.TRANSLATE, live.onTick(300))
        assertEquals(State.TRANSLATING, live.state)
        assertEquals(Action.NONE, live.onTick(400))
        assertTrue(live.onTranslated(900))
        assertEquals(State.SHOWING, live.state)
    }

    @Test
    fun `scrolling hides the translation and waits until it stops`() {
        val live = LiveScheduler(settleMs = 300, minGapMs = 0, graceMs = 200)
        live.start(0)
        live.onTick(300)
        live.onTranslated(500)
        assertEquals(Action.NONE, live.onScreenChanged(600)) // the overlay appearing
        assertEquals(Action.HIDE_OVERLAY, live.onScreenChanged(800))
        assertEquals(Action.NONE, live.onScreenChanged(900)) // still scrolling
        assertEquals(Action.NONE, live.onTick(1100))
        assertEquals(Action.TRANSLATE, live.onTick(1200))
    }

    @Test
    fun `a result that arrives after the screen changed is dropped`() {
        val live = LiveScheduler(settleMs = 300, minGapMs = 0, graceMs = 200)
        live.start(0)
        live.onTick(300)
        live.onScreenChanged(400)
        assertFalse(live.onTranslated(700))
        assertEquals(State.SETTLING, live.state)
        assertEquals(Action.NONE, live.onTick(600))
        assertEquals(Action.TRANSLATE, live.onTick(700))
    }

    @Test
    fun `translations are spaced by minGap and failures do not loop`() {
        val live = LiveScheduler(settleMs = 100, minGapMs = 1000, graceMs = 0)
        live.start(0)
        assertEquals(Action.TRANSLATE, live.onTick(1000))
        live.onFailed(1100)
        assertEquals(State.SHOWING, live.state)
        assertEquals(Action.HIDE_OVERLAY, live.onScreenChanged(1200))
        assertEquals(Action.NONE, live.onTick(1400)) // < minGap after the failure
        assertEquals(Action.TRANSLATE, live.onTick(2100))
    }

    @Test
    fun `tapping the button while live translates right away`() {
        val live = LiveScheduler(settleMs = 300, minGapMs = 5000, graceMs = 0)
        live.start(0)
        live.onTick(5000)
        live.onTranslated(5100)
        assertEquals(Action.HIDE_OVERLAY, live.forceNow(5200))
        assertEquals(Action.TRANSLATE, live.onTick(5200))
    }

    @Test
    fun `nothing happens once stopped`() {
        val live = LiveScheduler()
        live.start(0)
        live.stop()
        assertEquals(Action.NONE, live.onTick(10_000))
        assertEquals(Action.NONE, live.onScreenChanged(10_000))
        assertFalse(live.onTranslated(10_000))
    }

    @Test
    fun `a scroll keeps the translation and only translates what scrolled in`() {
        val live = LiveScheduler(settleMs = 300, moreSettleMs = 200, minGapMs = 0, graceMs = 100)
        live.start(0)
        assertEquals(Action.TRANSLATE, live.onTick(300))
        assertTrue(live.onTranslated(800))
        live.onScrolled(1000)
        assertEquals(State.TRACKING, live.state)
        live.onScrolled(1100) // still scrolling; frames 100 ms apart → still for 250 ms
        assertEquals(Action.NONE, live.onTick(1300))
        assertEquals(Action.TRANSLATE_MORE, live.onTick(1350))
        assertTrue(live.incremental)
        live.onScrolled(1400) // moved again while translating: result is shifted, not dropped
        assertTrue(live.onTranslated(1500))
        assertEquals(State.SHOWING, live.state)
    }

    @Test
    fun `a new page while tracking hides the translation and starts over`() {
        val live = LiveScheduler(settleMs = 300, moreSettleMs = 200, minGapMs = 0, graceMs = 100)
        live.start(0)
        live.onTick(300)
        live.onTranslated(800)
        live.onScrolled(1000)
        assertEquals(Action.HIDE_OVERLAY, live.onScreenChanged(1050))
        assertEquals(State.SETTLING, live.state)
        assertEquals(Action.TRANSLATE, live.onTick(1350))
        assertFalse(live.incremental)
    }

    @Test
    fun `a page change during an incremental run hides it and drops the result`() {
        val live = LiveScheduler(settleMs = 300, moreSettleMs = 200, minGapMs = 0, graceMs = 100)
        live.start(0)
        live.onTick(300)
        live.onTranslated(800)
        live.onScrolled(1000)
        live.onTick(1200)
        assertEquals(Action.HIDE_OVERLAY, live.onScreenChanged(1300))
        assertFalse(live.onTranslated(1400))
        assertEquals(State.SETTLING, live.state)
    }

    @Test
    fun `slow frames make the scroll count as stopped later`() {
        val live = LiveScheduler(settleMs = 300, moreSettleMs = 160, minGapMs = 0, graceMs = 0)
        live.start(0)
        live.onTick(300)
        live.onTranslated(400)
        live.onScrolled(1000)
        live.onScrolled(1300) // frames 300 ms apart: wait 750 ms, not 160
        assertEquals(Action.NONE, live.onTick(1500))
        assertEquals(Action.NONE, live.onTick(2000))
        assertEquals(Action.TRANSLATE_MORE, live.onTick(2050))
    }
}
