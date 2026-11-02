import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/painting.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/features/image_translation/domain/color_sampling.dart';
import 'package:vidtrans_mobile/features/image_translation/domain/image_languages.dart';
import 'package:vidtrans_mobile/features/image_translation/domain/image_translation_pipeline.dart';
import 'package:vidtrans_mobile/features/image_translation/domain/text_fit.dart';
import 'package:vidtrans_mobile/features/image_translation/domain/text_region.dart';

/// RGBA buffer filled with [background] and a [text]-coloured rectangle.
Uint8List _buffer(
  int w,
  int h,
  Color background, {
  Rect? glyphs,
  Color text = const Color(0xFF000000),
}) {
  final data = Uint8List(w * h * 4);
  int c(double v) => (v * 255).round();
  for (var y = 0; y < h; y++) {
    for (var x = 0; x < w; x++) {
      final inGlyph =
          glyphs != null && glyphs.contains(Offset(x + 0.5, y + 0.5));
      final color = inGlyph ? text : background;
      final i = (y * w + x) * 4;
      data[i] = c(color.r);
      data[i + 1] = c(color.g);
      data[i + 2] = c(color.b);
      data[i + 3] = 255;
    }
  }
  return data;
}

Future<Uint8List> _png(
  int w,
  int h,
  Color background,
  Rect glyphs,
  Color text,
) async {
  final recorder = ui.PictureRecorder();
  final canvas = Canvas(recorder);
  canvas.drawRect(
    Rect.fromLTWH(0, 0, w.toDouble(), h.toDouble()),
    Paint()..color = background,
  );
  canvas.drawRect(glyphs, Paint()..color = text);
  final image = await recorder.endRecording().toImage(w, h);
  final bytes = (await image.toByteData(
    format: ui.ImageByteFormat.png,
  ))!.buffer.asUint8List();
  image.dispose();
  return bytes;
}

void main() {
  group('colour sampling', () {
    test('reads a chat bubble fill and its dark text', () {
      const bubble = Color(0xFF95EC69); // WeChat green
      const ink = Color(0xFF1A1A1A);
      final rgba = _buffer(
        120,
        60,
        bubble,
        glyphs: const Rect.fromLTWH(30, 20, 50, 16),
        text: ink,
      );
      final colors = sampleRegionColors(
        rgba,
        120,
        60,
        const Rect.fromLTWH(26, 16, 60, 24),
      );
      expect(colors.background, bubble);
      expect(colors.foreground, ink);
    });

    test('falls back to a readable colour when text is invisible', () {
      const grey = Color(0xFF808080);
      final rgba = _buffer(80, 40, grey);
      final colors = sampleRegionColors(
        rgba,
        80,
        40,
        const Rect.fromLTWH(10, 10, 40, 16),
      );
      expect(
        contrastRatio(colors.foreground, colors.background),
        greaterThanOrEqualTo(3),
      );
    });

    test('a box touching the image edge is clamped', () {
      final rgba = _buffer(40, 40, const Color(0xFFFFFFFF));
      final colors = sampleRegionColors(
        rgba,
        40,
        40,
        const Rect.fromLTWH(-5, -5, 60, 60),
      );
      expect(colors.background, const Color(0xFFFFFFFF));
    });
  });

  group('background room', () {
    test('grows into the plain padding of a bubble and stops at its edge', () {
      // 200x120 grey screen, white bubble at (20,20)-(180,100), text inside.
      const grey = Color(0xFFEDEDED);
      final rgba = _buffer(
        200,
        120,
        grey,
        glyphs: const Rect.fromLTWH(20, 20, 160, 80),
        text: const Color(0xFFFFFFFF),
      );
      final room = expandToBackground(
        rgba,
        200,
        120,
        const Rect.fromLTWH(50, 50, 60, 20),
        const Color(0xFFFFFFFF),
        maxGrowX: 100,
        maxGrowY: 100,
        inset: 4,
      );
      expect(room, const Rect.fromLTRB(24, 24, 175, 95));
    });

    test('never shrinks below the text and respects the growth limit', () {
      final rgba = _buffer(300, 300, const Color(0xFFFFFFFF));
      final room = expandToBackground(
        rgba,
        300,
        300,
        const Rect.fromLTWH(100, 100, 50, 20),
        const Color(0xFFFFFFFF),
        maxGrowX: 10,
        maxGrowY: 5,
        inset: 0,
      );
      expect(room, const Rect.fromLTRB(90, 95, 160, 125));
    });
  });

  group('text fit', () {
    const image = Size(1080, 2400);

    test('a short translation keeps the original size', () {
      final fit = fitText(
        text: 'Xin chào',
        box: const Rect.fromLTWH(100, 100, 600, 60),
        lineHeight: 60,
        image: image,
      );
      expect(fit.fontSize, closeTo(48, 0.01));
      expect(fit.overflow, isFalse);
      expect(fit.paintBox, const Rect.fromLTWH(100, 100, 600, 60));
    });

    test('a long translation wraps and shrinks but stays readable', () {
      final fit = fitText(
        text:
            'Hôm nay bạn có thời gian họp không? Chúng ta cần bàn kế hoạch quý sau.',
        box: const Rect.fromLTWH(100, 100, 500, 150),
        lineHeight: 50,
        image: image,
      );
      expect(fit.fontSize, lessThan(40));
      expect(
        fit.fontSize,
        greaterThanOrEqualTo(image.width / 360 * 8.5 - 0.01),
      );
      expect(fit.overflow, isFalse);
    });

    test('a slightly long translation shrinks inside its box first', () {
      final fit = fitText(
        // The test font draws every glyph one font-size wide: 9 glyphs need
        // 432 px at 48 px but fit 360 px at 40 px.
        text: 'Họp không',
        box: const Rect.fromLTWH(100, 100, 360, 60),
        lineHeight: 60,
        image: image,
      );
      expect(fit.paintBox, const Rect.fromLTWH(100, 100, 360, 60));
      expect(fit.fontSize, greaterThanOrEqualTo(48 * 0.7 - 0.01));
      expect(fit.fontSize, lessThan(48));
    });

    test('a long translation moves into the room around it', () {
      final fit = fitText(
        text: 'Họp không nhé',
        box: const Rect.fromLTWH(120, 120, 200, 40),
        room: const Rect.fromLTWH(100, 100, 400, 120),
        lineHeight: 40,
        image: image,
      );
      expect(fit.paintBox, const Rect.fromLTWH(100, 100, 400, 120));
      expect(fit.overflow, isFalse);
    });

    test('a one-line box widens (within the image) before getting tiny', () {
      final fit = fitText(
        text: 'Hôm nay bạn có thời gian không',
        box: const Rect.fromLTWH(900, 10, 160, 40),
        lineHeight: 40,
        image: image,
      );
      expect(fit.paintBox.right, lessThanOrEqualTo(image.width));
      expect(fit.paintBox.width, greaterThan(160));
    });

    test('text that cannot fit grows downwards and is flagged', () {
      final fit = fitText(
        text: List.filled(12, 'một câu dịch rất dài').join(' '),
        box: const Rect.fromLTWH(100, 100, 300, 40),
        lineHeight: 40,
        image: image,
      );
      expect(fit.overflow, isTrue);
      expect(fit.paintBox.top, 100);
      expect(fit.paintBox.height, greaterThan(40));
    });
  });

  group('filters and languages', () {
    test('only real text in another language is translated', () {
      expect(shouldTranslate('你今天有时间开会吗？', 'zh-CN', 'vi'), isTrue);
      expect(shouldTranslate('好', 'zh-CN', 'vi'), isTrue);
      expect(shouldTranslate('10:45', 'en', 'vi'), isFalse);
      expect(shouldTranslate('Xin chào', 'vi', 'vi'), isFalse);
      expect(shouldTranslate('A', 'en', 'vi'), isFalse);
      expect(shouldTranslate('Hello', 'en', 'vi'), isTrue);
    });

    test('ML Kit codes map to backend codes', () {
      expect(languageFromMlKit('zh'), 'zh-CN');
      expect(languageFromMlKit('zh-Latn'), 'zh-CN');
      expect(languageFromMlKit('en'), 'en');
      expect(languageFromMlKit('und'), isNull);
      expect(guessLanguage('开会'), 'zh-CN');
      expect(guessLanguage('meeting'), 'en');
      expect(ocrScriptFor(autoLanguage), OcrScript.chinese);
      expect(ocrScriptFor('en'), OcrScript.latin);
    });
  });

  group('pipeline', () {
    test(
      'reads, groups by language, translates once per text and lays out',
      () async {
        final bytes = await _png(
          400,
          300,
          const Color(0xFFFFFFFF),
          const Rect.fromLTWH(40, 40, 120, 30),
          const Color(0xFF000000),
        );
        final calls = <String>[];
        final stages = <TranslationStage>[];
        final pipeline = ImageTranslationPipeline(
          ocr: (path, script) async {
            expect(script, OcrScript.chinese);
            return const [
              OcrBlock(
                text: '开会吗',
                box: Rect.fromLTWH(36, 36, 130, 38),
                lines: [Rect.fromLTWH(36, 36, 130, 38)],
              ),
              OcrBlock(
                text: 'Meeting',
                box: Rect.fromLTWH(36, 120, 130, 30),
                lines: [Rect.fromLTWH(36, 120, 130, 30)],
              ),
              OcrBlock(
                text: '开会吗',
                box: Rect.fromLTWH(36, 200, 130, 38),
                lines: [Rect.fromLTWH(36, 200, 130, 38)],
              ),
              OcrBlock(
                text: '12:30',
                box: Rect.fromLTWH(300, 10, 60, 20),
                lines: [Rect.fromLTWH(300, 10, 60, 20)],
              ),
              OcrBlock(
                text: 'Ngoài ảnh',
                box: Rect.fromLTWH(500, 500, 50, 20),
                lines: [Rect.fromLTWH(500, 500, 50, 20)],
              ),
            ];
          },
          detect: (text) async => guessLanguage(text),
        );
        final result = await pipeline.run(
          imagePath: 'unused.png',
          bytes: bytes,
          source: autoLanguage,
          target: 'vi',
          translate: (texts, {required source, required target}) async {
            calls.add('$source:${texts.join('|')}');
            return [for (final t in texts) 'vi($t)'];
          },
          onStage: stages.add,
        );
        addTearDown(result.dispose);

        expect(stages, TranslationStage.values);
        expect(calls, ['zh-CN:开会吗', 'en:Meeting']);
        expect(
          result.regions,
          hasLength(4),
          reason: 'the block outside the image is dropped',
        );
        expect(result.translatedCount, 3);
        expect(result.regions[0].translation, 'vi(开会吗)');
        expect(result.regions[0].background, const Color(0xFFFFFFFF));
        expect(result.regions[0].foreground, const Color(0xFF000000));
        expect(
          result.regions[3].translated,
          isFalse,
          reason: 'timestamps stay as they are',
        );
      },
    );

    test('large inputs are split into server-sized batches', () async {
      final bytes = await _png(
        100,
        100,
        const Color(0xFFFFFFFF),
        const Rect.fromLTWH(1, 1, 2, 2),
        const Color(0xFF000000),
      );
      final blocks = [
        for (var i = 0; i < 170; i++)
          OcrBlock(
            text: 'word $i',
            box: const Rect.fromLTWH(10, 10, 50, 10),
            lines: const [Rect.fromLTWH(10, 10, 50, 10)],
          ),
      ];
      final sizes = <int>[];
      final result =
          await ImageTranslationPipeline(
            ocr: (_, _) async => blocks,
            detect: (_) async => 'en',
          ).run(
            imagePath: 'unused.png',
            bytes: bytes,
            source: 'en',
            target: 'vi',
            translate: (texts, {required source, required target}) async {
              sizes.add(texts.length);
              return texts;
            },
          );
      result.dispose();
      expect(sizes, [80, 80, 10]);
    });
  });
}
