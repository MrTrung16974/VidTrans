import 'dart:math' as math;

import 'package:flutter/painting.dart';

/// Where and how big a translation is painted (design §5.4).
class FittedText {
  const FittedText({
    required this.fontSize,
    required this.paintBox,
    required this.overflow,
  });

  /// Font size in image pixels.
  final double fontSize;
  final Rect paintBox;

  /// The translation did not fit the original box even at the smallest
  /// readable size and was given more room below it.
  final bool overflow;
}

/// Line height multiplier used both for fitting and painting.
const translationLineHeight = 1.18;

TextStyle translationStyle(double fontSize, Color color) => TextStyle(
  fontSize: fontSize,
  height: translationLineHeight,
  color: color,
  fontWeight: FontWeight.w500,
);

double _height(String text, double fontSize, double width) {
  final painter = TextPainter(
    text: TextSpan(
      text: text,
      style: translationStyle(fontSize, const Color(0xFF000000)),
    ),
    textDirection: TextDirection.ltr,
  )..layout(maxWidth: math.max(1, width));
  final height = painter.height;
  painter.dispose();
  return height;
}

double _singleLineWidth(String text, double fontSize) {
  final painter = TextPainter(
    text: TextSpan(
      text: text,
      style: translationStyle(fontSize, const Color(0xFF000000)),
    ),
    textDirection: TextDirection.ltr,
    maxLines: 1,
  )..layout();
  final width = painter.width;
  painter.dispose();
  return width;
}

/// Fits [text] into [box] without shortening it:
///
/// 1. start near the original font size (from the OCR line height);
/// 2. wrap and shrink inside the box, down to 70% of that size, so the
///    translation sits exactly where the original text was;
/// 3. otherwise use [room] (the plain background around the text, e.g. the
///    padding of its chat bubble) and a one-line room may widen up to 40%;
/// 4. shrink further, down to the smallest readable size for this image;
/// 5. still too long: grow the box downwards and report [FittedText.overflow],
///    so the screen can also list it as plain text.
FittedText fitText({
  required String text,
  required Rect box,
  required double lineHeight,
  required Size image,
  Rect? room,
}) {
  // The original glyphs are ~80% of their line box.
  final start = math.max(1.0, lineHeight * 0.8);
  // Readable when the image is shown phone-wide (~360 logical px across).
  final minimum = math.min(
    start,
    math.max(lineHeight * 0.5, image.width / 360 * 8.5),
  );
  final comfortable = math.max(minimum, start * 0.7);

  // Largest size in [lo, hi] at which the text fits [width] x [height].
  double? largest(double lo, double hi, double width, double height) {
    if (_height(text, hi, width) <= height) return hi;
    if (_height(text, lo, width) > height) return null;
    for (var i = 0; i < 12 && hi - lo > 0.25; i++) {
      final mid = (lo + hi) / 2;
      if (_height(text, mid, width) <= height) {
        lo = mid;
      } else {
        hi = mid;
      }
    }
    return lo;
  }

  final snug = largest(comfortable, start, box.width, box.height);
  if (snug != null) {
    return FittedText(fontSize: snug, paintBox: box, overflow: false);
  }

  final area = room ?? box;
  final roomy = largest(comfortable, start, area.width, area.height);
  if (roomy != null) {
    return FittedText(fontSize: roomy, paintBox: area, overflow: false);
  }

  var width = area.width;
  if (area.height < lineHeight * 1.6) {
    final wanted = _singleLineWidth(text, comfortable);
    if (wanted > width) {
      width = math.min(
        math.max(width, math.min(wanted, area.width * 1.4)),
        image.width - area.left,
      );
    }
  }
  final fitted = largest(minimum, start, width, area.height);
  if (fitted != null) {
    return FittedText(
      fontSize: fitted,
      paintBox: Rect.fromLTWH(area.left, area.top, width, area.height),
      overflow: false,
    );
  }

  final needed = _height(text, minimum, width);
  final bottom = math.min(image.height, area.top + needed);
  final top = math.max(0.0, bottom - needed);
  return FittedText(
    fontSize: minimum,
    paintBox: Rect.fromLTRB(area.left, top, area.left + width, bottom),
    overflow: true,
  );
}
