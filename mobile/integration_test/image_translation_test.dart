// On-device smoke test of image translation with the real ML Kit engines:
//   flutter test integration_test/image_translation_test.dart -d <android device>
// Draws a WeChat-like chat, reads it with OCR, translates it offline
// (downloads the zh/vi models once, ~60 MB) and writes the result PNG to the
// app's documents directory as image_translation_result.png.
import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:path_provider/path_provider.dart';
import 'package:vidtrans_mobile/features/image_translation/data/block_translators.dart';
import 'package:vidtrans_mobile/features/image_translation/data/ocr_service.dart';
import 'package:vidtrans_mobile/features/image_translation/domain/image_languages.dart';
import 'package:vidtrans_mobile/features/image_translation/domain/image_translation_pipeline.dart';
import 'package:vidtrans_mobile/features/image_translation/presentation/widgets/translated_image.dart';

const _messages = [
  ('你今天有时间开会吗？', Color(0xFFFFFFFF), false),
  ('我们下午三点在会议室见面吧', Color(0xFF95EC69), true),
  ('好的，没问题', Color(0xFFFFFFFF), false),
];

Future<List<int>> _drawChat() async {
  const width = 1080.0, height = 1200.0;
  final recorder = ui.PictureRecorder();
  final canvas = Canvas(recorder);
  canvas.drawRect(
    const Rect.fromLTWH(0, 0, width, height),
    Paint()..color = const Color(0xFFEDEDED),
  );
  var top = 120.0;
  for (final (text, bubble, mine) in _messages) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: const TextStyle(fontSize: 46, color: Color(0xFF191919)),
      ),
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: 700);
    final box = Rect.fromLTWH(
      mine ? width - painter.width - 140 : 140,
      top,
      painter.width + 60,
      painter.height + 50,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(box, const Radius.circular(14)),
      Paint()..color = bubble,
    );
    painter.paint(canvas, box.topLeft + const Offset(30, 25));
    top = box.bottom + 80;
  }
  final image = await recorder.endRecording().toImage(
    width.toInt(),
    height.toInt(),
  );
  final png = await image.toByteData(format: ui.ImageByteFormat.png);
  image.dispose();
  return png!.buffer.asUint8List();
}

Future<File> _writeInput() async {
  final dir = await getApplicationDocumentsDirectory();
  final input = File('${dir.path}/image_translation_input.png');
  await input.writeAsBytes(await _drawChat(), flush: true);
  return input;
}

void _log(ImageTranslationResult result, int ms) {
  for (final region in result.regions) {
    debugPrint(
      'OCR "${region.sourceText}" [${region.language}] ${region.box} -> "${region.translation}" '
      'font=${region.fontSize.toStringAsFixed(1)} bg=${region.background} fg=${region.foreground} '
      'overflow=${region.overflow} paint=${region.paintBox}',
    );
  }
  debugPrint('pipeline took $ms ms');
}

/// `--dart-define=DUMP_PNG=true` prints rendered PNGs as base64 so they
/// survive the uninstall at the end of `flutter test`.
void _dump(String name, List<int> png) {
  if (!const bool.fromEnvironment('DUMP_PNG')) return;
  final data = base64Encode(png);
  for (var i = 0; i < data.length; i += 900) {
    debugPrint(
      'PNG:$name:${data.substring(i, i + 900 > data.length ? data.length : i + 900)}',
    );
  }
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('real OCR reads the chat, colours and layout are right', (
    tester,
  ) async {
    await tester.runAsync(() async {
      final input = await _writeInput();
      final ocr = OcrService();
      final detector = BlockLanguageDetector();
      addTearDown(ocr.close);
      addTearDown(detector.close);

      final watch = Stopwatch()..start();
      final result =
          await ImageTranslationPipeline(
            ocr: ocr.recognize,
            detect: detector.detect,
          ).run(
            imagePath: input.path,
            bytes: await input.readAsBytes(),
            source: autoLanguage,
            target: 'vi',
            // Deterministic stand-in: this test checks OCR and layout, not wording.
            translate: (texts, {required source, required target}) async => [
              for (final text in texts)
                'Bản dịch thử của một câu khá dài: ${text.length} ký tự',
            ],
          );
      watch.stop();
      addTearDown(result.dispose);
      _log(result, watch.elapsedMilliseconds);

      final dir = await getApplicationDocumentsDirectory();
      final png = await renderTranslatedPng(result);
      await File(
        '${dir.path}/image_translation_ocr.png',
      ).writeAsBytes(png, flush: true);
      _dump('input', await input.readAsBytes());
      _dump('ocr', png);
      // debugPrint is throttled; give the log time to drain before uninstall.
      if (const bool.fromEnvironment('DUMP_PNG')) {
        await Future<void>.delayed(const Duration(seconds: 25));
      }

      final texts = result.regions
          .map((region) => region.sourceText)
          .join('\n');
      for (final (message, _, _) in _messages) {
        // OCR may swap punctuation width or read a glyph in its traditional
        // form (题 -> 題), so compare the first characters only.
        final head = message.replaceAll(RegExp(r'[，,？?]'), '').substring(0, 3);
        expect(texts.replaceAll(RegExp(r'[，,？?]'), ''), contains(head));
      }
      expect(result.translatedCount, greaterThanOrEqualTo(_messages.length));
      expect(
        result.regions.every((region) => region.language == 'zh-CN'),
        isTrue,
      );
      final bubbles = result.regions.map((region) => region.background).toSet();
      expect(
        bubbles,
        containsAll(const [Color(0xFFFFFFFF), Color(0xFF95EC69)]),
      );
    });
  });

  testWidgets(
    'offline ML Kit translation (needs internet once for the models)',
    (tester) async {
      await tester.runAsync(() async {
        final translator = OfflineBlockTranslator();
        final missing = await translator.missingModels(['zh-CN', 'vi']);
        if (missing.isNotEmpty) {
          try {
            await translator
                .download(missing, wifiOnly: false)
                .timeout(const Duration(seconds: 90));
          } on Object catch (error) {
            markTestSkipped(
              'zh/vi models could not be downloaded on this device: $error',
            );
            return;
          }
        }
        final input = await _writeInput();
        final ocr = OcrService();
        final detector = BlockLanguageDetector();
        addTearDown(ocr.close);
        addTearDown(detector.close);
        final watch = Stopwatch()..start();
        final result =
            await ImageTranslationPipeline(
              ocr: ocr.recognize,
              detect: detector.detect,
            ).run(
              imagePath: input.path,
              bytes: await input.readAsBytes(),
              source: autoLanguage,
              target: 'vi',
              translate: translator.translate,
            );
        watch.stop();
        addTearDown(result.dispose);
        _log(result, watch.elapsedMilliseconds);
        final dir = await getApplicationDocumentsDirectory();
        await File(
          '${dir.path}/image_translation_result.png',
        ).writeAsBytes(await renderTranslatedPng(result), flush: true);
        expect(result.translatedCount, greaterThanOrEqualTo(_messages.length));
        expect(
          result.regions
              .where((region) => region.translated)
              .every(
                (region) =>
                    !RegExp(r'[\u3400-\u9fff]').hasMatch(region.translation!),
              ),
          isTrue,
          reason: 'translations are Vietnamese, not the Chinese source',
        );
      });
    },
  );
}
