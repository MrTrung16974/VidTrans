import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui';

/// Background and text colour around one block of text ("adaptive
/// background" from the design: no inpainting, a solid cover that matches).
class RegionColors {
  const RegionColors(this.background, this.foreground);

  final Color background;
  final Color foreground;
}

/// Reads colours from an RGBA image buffer (`width * height * 4` bytes).
///
/// Background: per-channel median of a thin ring just outside [box], where
/// chat bubbles, cards and panels show their plain fill. Text: the average of
/// the pixels inside [box] that differ most from that background. When the
/// text colour is too close to the background (anti-aliased thin glyphs, a
/// photo behind), black or white is used instead so the translation reads.
RegionColors sampleRegionColors(
  Uint8List rgba,
  int width,
  int height,
  Rect box,
) {
  final background =
      _ringMedian(rgba, width, height, box) ?? const Color(0xFFFFFFFF);
  final foreground = _textColor(rgba, width, height, box, background);
  return RegionColors(background, foreground);
}

const _ring = 3;
const _maxSamples = 600;

Color? _ringMedian(Uint8List rgba, int width, int height, Rect box) {
  final left = math.max(0, box.left.floor() - _ring);
  final top = math.max(0, box.top.floor() - _ring);
  final right = math.min(width - 1, box.right.ceil() + _ring);
  final bottom = math.min(height - 1, box.bottom.ceil() + _ring);
  if (right <= left || bottom <= top) return null;

  final perimeter = 2 * ((right - left) + (bottom - top));
  final step = math.max(1, perimeter ~/ _maxSamples);
  final r = <int>[], g = <int>[], b = <int>[];
  void add(int x, int y) {
    final i = (y * width + x) * 4;
    r.add(rgba[i]);
    g.add(rgba[i + 1]);
    b.add(rgba[i + 2]);
  }

  for (var x = left; x <= right; x += step) {
    add(x, top);
    add(x, bottom);
  }
  for (var y = top; y <= bottom; y += step) {
    add(left, y);
    add(right, y);
  }
  return Color.fromARGB(255, _median(r), _median(g), _median(b));
}

int _median(List<int> values) {
  values.sort();
  return values[values.length ~/ 2];
}

Color _textColor(
  Uint8List rgba,
  int width,
  int height,
  Rect box,
  Color background,
) {
  final left = math.max(0, box.left.floor());
  final top = math.max(0, box.top.floor());
  final right = math.min(width, box.right.ceil());
  final bottom = math.min(height, box.bottom.ceil());
  final area = (right - left) * (bottom - top);
  if (area <= 0) return _contrasting(background);

  final step = math.max(1, math.sqrt(area / 2500).floor());
  final br = _r(background), bg = _g(background), bb = _b(background);
  final candidates = <(int, int, int, int)>[];
  for (var y = top; y < bottom; y += step) {
    for (var x = left; x < right; x += step) {
      final i = (y * width + x) * 4;
      final dr = rgba[i] - br, dg = rgba[i + 1] - bg, db = rgba[i + 2] - bb;
      final distance = dr * dr + dg * dg + db * db;
      if (distance > 60 * 60) {
        candidates.add((distance, rgba[i], rgba[i + 1], rgba[i + 2]));
      }
    }
  }
  if (candidates.length < 4) return _contrasting(background);
  candidates.sort((a, b) => b.$1.compareTo(a.$1));
  final top30 = candidates
      .take(math.max(4, candidates.length * 3 ~/ 10))
      .toList();
  var sr = 0, sg = 0, sb = 0;
  for (final c in top30) {
    sr += c.$2;
    sg += c.$3;
    sb += c.$4;
  }
  final n = top30.length;
  final text = Color.fromARGB(255, sr ~/ n, sg ~/ n, sb ~/ n);
  return contrastRatio(text, background) >= 3 ? text : _contrasting(background);
}

int _r(Color c) => (c.r * 255).round();
int _g(Color c) => (c.g * 255).round();
int _b(Color c) => (c.b * 255).round();

Color _contrasting(Color background) =>
    contrastRatio(const Color(0xFF111111), background) >=
        contrastRatio(const Color(0xFFFFFFFF), background)
    ? const Color(0xFF111111)
    : const Color(0xFFFFFFFF);

/// WCAG contrast ratio between two opaque colours (1..21).
double contrastRatio(Color a, Color b) {
  final la = a.computeLuminance(), lb = b.computeLuminance();
  final hi = math.max(la, lb), lo = math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

/// Grows [box] outwards while the pixels just outside it still show
/// [background] (the padding of a chat bubble, card or button), at most
/// [maxGrowX] / [maxGrowY] pixels per side, keeping [inset] pixels clear of
/// the edge it stopped at. A longer translation can then use that free space
/// instead of spilling out of its bubble.
Rect expandToBackground(
  Uint8List rgba,
  int width,
  int height,
  Rect box,
  Color background, {
  required double maxGrowX,
  required double maxGrowY,
  double inset = 6,
}) {
  final br = _r(background), bg = _g(background), bb = _b(background);
  bool plain(int x0, int y0, int x1, int y1) {
    // Sampled segment from (x0, y0) to (x1, y1), horizontal or vertical.
    final length = math.max((x1 - x0).abs(), (y1 - y0).abs());
    if (length <= 0) return false;
    final step = math.max(1, length ~/ 60);
    var total = 0, matching = 0;
    for (var t = 0; t <= length; t += step) {
      final x = x0 + (x1 - x0) * t ~/ length;
      final y = y0 + (y1 - y0) * t ~/ length;
      if (x < 0 || y < 0 || x >= width || y >= height) return false;
      final i = (y * width + x) * 4;
      final dr = rgba[i] - br, dg = rgba[i + 1] - bg, db = rgba[i + 2] - bb;
      total++;
      if (dr * dr + dg * dg + db * db <= 28 * 28) matching++;
    }
    return matching >= total * 0.97;
  }

  // OCR boxes hug the glyphs, whose anti-aliased edges spill a pixel or two
  // outside; start the search just beyond them.
  const glyphEdge = 3;
  var left = math.max(0, box.left.floor() - glyphEdge);
  var top = math.max(0, box.top.floor() - glyphEdge);
  var right = math.min(width - 1, box.right.ceil() + glyphEdge);
  var bottom = math.min(height - 1, box.bottom.ceil() + glyphEdge);
  var stopLeft = false, stopTop = false, stopRight = false, stopBottom = false;
  for (
    var grown = glyphEdge + 1;
    grown <= math.max(maxGrowX, maxGrowY);
    grown++
  ) {
    if (!stopLeft && grown <= maxGrowX) {
      plain(left - 1, top, left - 1, bottom) ? left-- : stopLeft = true;
    }
    if (!stopRight && grown <= maxGrowX) {
      plain(right + 1, top, right + 1, bottom) ? right++ : stopRight = true;
    }
    if (!stopTop && grown <= maxGrowY) {
      plain(left, top - 1, right, top - 1) ? top-- : stopTop = true;
    }
    if (!stopBottom && grown <= maxGrowY) {
      plain(left, bottom + 1, right, bottom + 1) ? bottom++ : stopBottom = true;
    }
  }
  // Keep clear of the bubble edge, but never shrink below the text itself.
  return Rect.fromLTRB(
    math.min(box.left, left + inset),
    math.min(box.top, top + inset),
    math.max(box.right, right - inset),
    math.max(box.bottom, bottom - inset),
  );
}
