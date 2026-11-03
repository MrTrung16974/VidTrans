package cloud.vidtrans.app.screentranslator

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * One frame summarised row by row: for every screen row in [top, bottom),
 * the brightness of [cols] sample columns spread over the width. [MASKED]
 * marks samples under our own translation overlay: they show our drawing,
 * not the app, and must not take part in matching.
 */
class RowProfile(val top: Int, val bottom: Int, val cols: Int, val values: IntArray) {
    val rows get() = bottom - top

    companion object {
        const val MASKED = -1
    }
}

/** How the content moved between two frames. */
class ScrollShift(
    /** Pixels the content moved down (negative: up, i.e. the user scrolled down). */
    val dy: Int,
    /** True when the frames really are the same content moved by [dy]. */
    val confident: Boolean,
    private val fixedBlocks: BooleanArray,
    private val top: Int,
    /** Match cost at [dy] and without moving (diagnostics, minor-change test). */
    val cost: Double = 0.0,
    val stillCost: Double = 0.0,
) {
    /** Rows that did not move with the content (toolbars, input bars). */
    fun isFixed(screenY: Float): Boolean {
        val block = ((screenY - top) / ScrollEstimator.BLOCK).toInt()
        return block in fixedBlocks.indices && fixedBlocks[block]
    }

    override fun toString() =
        "dy=$dy confident=$confident cost=${"%.1f".format(cost)} still=${"%.1f".format(stillCost)}"
}

/**
 * Finds the vertical scroll between two frames so the live translation can
 * move with the text instead of disappearing while the user scrolls.
 *
 * Coarse-to-fine: offsets are scored on every 4th row, the best few again on
 * every row. A [hint] (the last frame's scroll: flings are smooth) narrows
 * the search to ±[HINT_REACH] first; the whole range is searched only when
 * that does not give a confident match. Masked samples (under the overlay)
 * are skipped, and each difference is capped so fixed bars cannot outvote
 * the moving content.
 */
object ScrollEstimator {
    const val BLOCK = 24

    /**
     * Below this "nothing moved" cost (mean difference per visible sample) a
     * change is small — a scrollbar fading in, a toolbar shadow, a spinner:
     * keep the translation and only look for new text. A page change costs
     * far more.
     */
    const val MINOR_CHANGE = 2.5 // measured: still ≈1.5, a chat scrolled by 500 px ≈5.4

    /** Mean difference below which a shift is the same content, not a look-alike. */
    const val MATCH = 3.0

    private const val COARSE = 4
    private const val CAP = 40
    private const val HINT_REACH = 120

    /**
     * Mean capped difference per visible sample, or MAX when too little
     * overlaps. With [trim], over the best-matching 80% of rows only: fixed
     * bars (title bar, message input, browser toolbar — ~15% of a chat
     * screen) cannot spoil a true scroll. Without it every row counts, which
     * is what "did the screen change at all" must look at: most rows of a chat
     * are empty background, so trimming would hide the text rows that moved.
     */
    private fun cost(
        prev: RowProfile, cur: RowProfile, dy: Int, minOverlap: Int, step: Int = 1, trim: Boolean = true,
    ): Double {
        val rows = cur.rows
        val cols = cur.cols
        // cur(y) = prev(y - dy)
        val from = max(0, dy)
        val to = min(rows, rows + dy)
        if (to - from < minOverlap) return Double.MAX_VALUE
        val a = cur.values
        val b = prev.values
        val rowCost = DoubleArray((to - from + step - 1) / step)
        var n = 0
        var visible = 0
        var y = from
        while (y < to) {
            val c = y * cols
            val p = (y - dy) * cols
            var total = 0
            var counted = 0
            for (k in 0 until cols) {
                val x = a[c + k]
                val z = b[p + k]
                if (x < 0 || z < 0) continue
                total += min(CAP, abs(x - z))
                counted++
            }
            if (counted >= cols / 4) {
                rowCost[n++] = total.toDouble() / counted
                visible += counted
            }
            y += step
        }
        // Too few visible samples to judge (almost all under the overlay).
        if (visible < ((to - from) / step) * cols / 8 || n == 0) return Double.MAX_VALUE
        val kept = if (trim) max(1, n * 4 / 5) else n
        if (trim) java.util.Arrays.sort(rowCost, 0, n)
        var sum = 0.0
        for (i in 0 until kept) sum += rowCost[i]
        return sum / kept
    }

    private fun search(prev: RowProfile, cur: RowProfile, range: IntRange, minOverlap: Int): Pair<Int, Double> {
        val candidates = ArrayList<Pair<Int, Double>>()
        for (d in range) {
            val c = cost(prev, cur, d, minOverlap, COARSE)
            if (candidates.size < 6 || c < candidates.last().second) {
                candidates.add(d to c)
                candidates.sortBy { it.second }
                if (candidates.size > 6) candidates.removeAt(6)
            }
        }
        val refined = ArrayList<Pair<Int, Double>>()
        for ((candidate, _) in candidates) {
            var dy = candidate
            var best = Double.MAX_VALUE
            for (d in (candidate - 1)..(candidate + 1)) {
                val c = cost(prev, cur, d, minOverlap)
                if (c < best) {
                    best = c
                    dy = d
                }
            }
            refined.add(dy to best)
        }
        // Content that repeats (identical rows a page apart) matches equally
        // well in several places: the user scrolled the shortest of them.
        val best = refined.minOf { it.second }
        return refined.filter { it.second <= best + 0.3 }.minBy { kotlin.math.abs(it.first) }
    }

    /**
     * Whether the rows [top, bottom) (screen y) of [base] are found [move]
     * pixels lower in [now]: the text a translation covers is still under it.
     * False when it vanished (a toolbar that hid itself) or the translation
     * would land on other text.
     */
    fun regionMatches(base: RowProfile, now: RowProfile, top: Float, bottom: Float, move: Int): Boolean {
        if (base.top != now.top || base.bottom != now.bottom || base.cols != now.cols) return false
        val cols = base.cols
        var total = 0L
        var counted = 0
        var y = top.toInt()
        while (y < bottom.toInt()) {
            val from = y - base.top
            val to = y + move - now.top
            if (from in 0 until base.rows && to in 0 until now.rows) {
                for (k in 0 until cols) {
                    val x = base.values[from * cols + k]
                    val z = now.values[to * cols + k]
                    if (x < 0 || z < 0) continue
                    total += min(CAP, abs(x - z))
                    counted++
                }
            }
            y++
        }
        // Mostly off-screen now: nothing to check against, keep it (clipped).
        if (counted < cols * 4) return true
        return total.toDouble() / counted < MATCH * 1.5
    }

    /** How different two frames are without any scroll (cheap: one offset). */
    fun stillCost(prev: RowProfile, cur: RowProfile): Double =
        if (prev.top != cur.top || prev.bottom != cur.bottom || prev.cols != cur.cols) Double.MAX_VALUE
        else cost(prev, cur, 0, cur.rows / 2, trim = false)

    fun estimate(prev: RowProfile, cur: RowProfile, hint: Int? = null, maxShift: Int = cur.rows / 3): ScrollShift? {
        if (prev.top != cur.top || prev.bottom != cur.bottom || prev.cols != cur.cols) return null
        val rows = cur.rows
        if (rows < BLOCK * 4) return null
        val minOverlap = rows / 2
        val still = cost(prev, cur, 0, minOverlap, trim = false)

        // Two clean frames of the same content moved by dy match almost exactly
        // (mean difference ~0.5–3). A look-alike position (the neighbouring chat
        // bubble, a repeated layout) matches far worse: demand both a close
        // absolute match and a clear win over "nothing moved".
        // And when the screen barely changed at all, it did not scroll: a
        // "match" elsewhere is a look-alike (repeated bubbles), not a move.
        fun isConfident(dy: Int, c: Double) =
            dy != 0 && c < MATCH && c < still * 0.5 && still >= MINOR_CHANGE

        var dy = 0
        var best = Double.MAX_VALUE
        if (hint != null) {
            val near = search(prev, cur, max(-maxShift, hint - HINT_REACH)..min(maxShift, hint + HINT_REACH), minOverlap)
            dy = near.first
            best = near.second
        }
        if (!isConfident(dy, best)) {
            val full = search(prev, cur, -maxShift..maxShift, minOverlap)
            if (full.second < best) {
                dy = full.first
                best = full.second
            }
        }

        // Fixed bands: row blocks that match better without moving.
        val blocks = (rows + BLOCK - 1) / BLOCK
        val fixed = BooleanArray(blocks)
        if (dy != 0) {
            val cols = cur.cols
            for (block in 0 until blocks) {
                var atZero = 0L
                var atShift = 0L
                var samples = 0
                val end = min(rows, (block + 1) * BLOCK)
                for (y in block * BLOCK until end) {
                    val ys = y - dy
                    for (k in 0 until cols) {
                        val x = cur.values[y * cols + k]
                        val z0 = prev.values[y * cols + k]
                        if (x < 0 || z0 < 0) continue
                        samples++
                        atZero += min(CAP, abs(x - z0))
                        val zs = if (ys in 0 until rows) prev.values[ys * cols + k] else -1
                        atShift += if (zs < 0) CAP else min(CAP, abs(x - zs))
                    }
                }
                // Fixed only with enough visible samples and a clear verdict: a
                // mostly masked or flat block moves with the content by default.
                fixed[block] = samples >= BLOCK * cols / 4 && atZero * 3 < atShift
            }
        }
        return ScrollShift(dy, isConfident(dy, best), fixed, cur.top, best, still)
    }
}
