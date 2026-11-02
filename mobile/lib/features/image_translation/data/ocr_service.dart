import 'package:google_mlkit_text_recognition/google_mlkit_text_recognition.dart'
    as mlkit;

import '../domain/image_languages.dart';
import '../domain/text_region.dart';

/// On-device OCR (Google ML Kit). The image never leaves the phone.
class OcrService {
  final _recognizers = <OcrScript, mlkit.TextRecognizer>{};

  Future<List<OcrBlock>> recognize(String imagePath, OcrScript script) async {
    final recognizer = _recognizers.putIfAbsent(
      script,
      () => mlkit.TextRecognizer(
        script: script == OcrScript.chinese
            ? mlkit.TextRecognitionScript.chinese
            : mlkit.TextRecognitionScript.latin,
      ),
    );
    final result = await recognizer.processImage(
      mlkit.InputImage.fromFilePath(imagePath),
    );
    return [
      for (final block in result.blocks)
        if (block.text.trim().isNotEmpty)
          OcrBlock(
            text: block.lines
                .map((line) => line.text.trim())
                .join(_joiner(block.text)),
            box: block.boundingBox,
            lines: [for (final line in block.lines) line.boundingBox],
          ),
    ];
  }

  /// Chinese lines join without a space; Latin lines with one.
  static String _joiner(String text) =>
      RegExp(r'[㐀-鿿]').hasMatch(text) ? '' : ' ';

  Future<void> close() async {
    for (final recognizer in _recognizers.values) {
      await recognizer.close();
    }
    _recognizers.clear();
  }
}
