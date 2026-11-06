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
  const _TranslatedImagePainter(
    this.result,
    this.showOriginal, {
    this.selectedRegionId,
    this.showRegionBadges = true,
  });

  final ImageTranslationResult result;
  final bool showOriginal;
  final int? selectedRegionId;
  final bool showRegionBadges;

  @override
  void paint(Canvas canvas, Size size) {
    paintTranslatedImage(canvas, result, showOriginal: showOriginal);
    if (showOriginal) return;

    final translated = result.regions.where((r) => r.translated).toList();
    for (final (index, region) in translated.indexed) {
      final box = region.paintBox ?? region.box;
      final isSelected = selectedRegionId == region.id;
      if (isSelected) {
        final highlightPaint = Paint()
          ..color = const Color(0xFFEA580C)
          ..style = PaintingStyle.stroke
          ..strokeWidth = math.max(2.0, result.size.width / 240);
        canvas.drawRRect(
          RRect.fromRectAndRadius(
            box.inflate(4),
            Radius.circular(math.max(4.0, region.fontSize * 0.25)),
          ),
          highlightPaint,
        );
      }
      if (showRegionBadges) {
        final badgeScale = (result.size.width / 380).clamp(0.8, 3.0);
        final badgeFontSize = 11.0 * badgeScale;
        final badgePainter = TextPainter(
          text: TextSpan(
            text: '${index + 1}',
            style: TextStyle(
              color: Colors.white,
              fontSize: badgeFontSize,
              fontWeight: FontWeight.w800,
              height: 1.0,
            ),
          ),
          textDirection: TextDirection.ltr,
        )..layout();
        final padX = 5.0 * badgeScale;
        final padY = 3.0 * badgeScale;
        final bw = math.max(badgePainter.width + padX * 2, 18.0 * badgeScale);
        final bh = badgePainter.height + padY * 2;
        final bx = (box.left - bw * 0.35).clamp(0.0, size.width - bw);
        final by = (box.top - bh * 0.55).clamp(0.0, size.height - bh);
        final badgeRect = RRect.fromRectAndRadius(
          Rect.fromLTWH(bx, by, bw, bh),
          Radius.circular(bh / 2),
        );
        canvas.drawRRect(
          badgeRect,
          Paint()
            ..color = isSelected
                ? const Color(0xFFEA580C)
                : const Color(0xCC0F172A),
        );
        badgePainter.paint(
          canvas,
          Offset(
            bx + (bw - badgePainter.width) / 2,
            by + (bh - badgePainter.height) / 2,
          ),
        );
        badgePainter.dispose();
      }
    }
  }

  @override
  bool shouldRepaint(covariant _TranslatedImagePainter old) =>
      old.result != result ||
      old.showOriginal != showOriginal ||
      old.selectedRegionId != selectedRegionId ||
      old.showRegionBadges != showRegionBadges;
}

/// The result scaled to the available width, pixel coordinates preserved.
class TranslatedImageView extends StatelessWidget {
  const TranslatedImageView({
    super.key,
    required this.result,
    this.showOriginal = false,
    this.selectedRegionId,
    this.showRegionBadges = true,
    this.onSelectRegion,
  });

  final ImageTranslationResult result;
  final bool showOriginal;
  final int? selectedRegionId;
  final bool showRegionBadges;
  final ValueChanged<int>? onSelectRegion;

  @override
  Widget build(BuildContext context) {
    return FittedBox(
      fit: BoxFit.contain,
      child: SizedBox.fromSize(
        size: result.size,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTapUp: onSelectRegion == null
              ? null
              : (details) {
                  final pos = details.localPosition;
                  for (final region in result.regions.reversed) {
                    if (!region.translated) continue;
                    final box = (region.paintBox ?? region.box).inflate(6);
                    if (box.contains(pos)) {
                      onSelectRegion!(region.id);
                      return;
                    }
                  }
                },
          child: CustomPaint(
            painter: _TranslatedImagePainter(
              result,
              showOriginal,
              selectedRegionId: selectedRegionId,
              showRegionBadges: showRegionBadges,
            ),
          ),
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
