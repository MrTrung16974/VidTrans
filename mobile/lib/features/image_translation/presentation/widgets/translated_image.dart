import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import '../../domain/image_translation_pipeline.dart';
import '../../domain/text_fit.dart';
import '../../domain/text_region.dart';

/// Paints the screenshot and, unless [showOriginal], covers every translated
/// block with its sampled background and draws the translation on top.
/// Coordinates are image pixels; the same code renders the exported PNG.
void paintTranslatedImage(
  Canvas canvas,
  ImageTranslationResult result, {
  bool showOriginal = false,
}) {
  canvas.drawImage(
    result.image,
    Offset.zero,
    Paint()..filterQuality = FilterQuality.medium,
  );
  if (showOriginal) return;
  for (final region in result.regions) {
    if (!region.translated) continue;
    final box = region.paintBox ?? region.box;
    final cover = Paint()..color = region.background;
    final radius = Radius.circular(region.fontSize * 0.2);
    // Glyph edges reach a little past the OCR boxes.
    final grow = math.max(3.0, region.fontSize * 0.12);
    for (final line in region.lines) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(line.inflate(grow), radius),
        cover,
      );
    }
    canvas.drawRRect(RRect.fromRectAndRadius(box.inflate(2), radius), cover);

    final painter = TextPainter(
      text: TextSpan(
        text: region.translation,
        style: translationStyle(region.fontSize, region.foreground),
      ),
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: box.width);
    final dy =
        box.top + ((box.height - painter.height) / 2).clamp(0, double.infinity);
    painter.paint(canvas, Offset(box.left, dy));
    painter.dispose();
  }
}

class _TranslatedImagePainter extends CustomPainter {
  const _TranslatedImagePainter(this.result, this.showOriginal);

  final ImageTranslationResult result;
  final bool showOriginal;

  @override
  void paint(Canvas canvas, Size size) =>
      paintTranslatedImage(canvas, result, showOriginal: showOriginal);

  @override
  bool shouldRepaint(covariant _TranslatedImagePainter old) =>
      old.result != result || old.showOriginal != showOriginal;
}

/// The result scaled to the available width, pixel coordinates preserved.
class TranslatedImageView extends StatelessWidget {
  const TranslatedImageView({
    super.key,
    required this.result,
    this.showOriginal = false,
  });

  final ImageTranslationResult result;
  final bool showOriginal;

  @override
  Widget build(BuildContext context) {
    return FittedBox(
      fit: BoxFit.contain,
      child: SizedBox.fromSize(
        size: result.size,
        child: CustomPaint(
          painter: _TranslatedImagePainter(result, showOriginal),
        ),
      ),
    );
  }
}

/// Full-resolution PNG of the translated image, for sharing or saving.
Future<Uint8List> renderTranslatedPng(ImageTranslationResult result) async {
  final recorder = ui.PictureRecorder();
  paintTranslatedImage(Canvas(recorder), result);
  final picture = recorder.endRecording();
  final image = await picture.toImage(result.image.width, result.image.height);
  picture.dispose();
  try {
    final data = await image.toByteData(format: ui.ImageByteFormat.png);
    return data!.buffer.asUint8List();
  } finally {
    image.dispose();
  }
}

/// Regions whose translation did not fit and grew; listed for the user too.
Iterable<TextRegion> overflowRegions(ImageTranslationResult result) =>
    result.regions.where((region) => region.overflow);
