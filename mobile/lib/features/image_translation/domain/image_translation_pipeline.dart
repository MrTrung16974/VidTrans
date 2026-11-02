import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'color_sampling.dart';
import 'image_languages.dart';
import 'text_fit.dart';
import 'text_region.dart';

/// Steps shown to the user while an image is processed.
enum TranslationStage { reading, detecting, translating, layout }

typedef OcrStep =
    Future<List<OcrBlock>> Function(String imagePath, OcrScript script);
typedef DetectStep = Future<String> Function(String text);
typedef TranslateStep =
    Future<List<String>> Function(
      List<String> texts, {
      required String source,
      required String target,
    });

class ImageTranslationResult {
  ImageTranslationResult({required this.image, required this.regions});

  final ui.Image image;
  final List<TextRegion> regions;

  ui.Size get size => ui.Size(image.width.toDouble(), image.height.toDouble());
  int get translatedCount =>
      regions.where((region) => region.translated).length;
  int get overflowCount => regions.where((region) => region.overflow).length;

  void dispose() => image.dispose();
}

/// Upper bounds of one server call (backend MAX_TEXT_TRANSLATION_*).
const _batchItems = 80;
const _batchChars = 11000;

/// OCR → filter → language → translate → colours → layout (design §4, §5).
///
/// The OCR, language and translation engines are passed in, so the
/// orchestration is tested without ML Kit or a network.
class ImageTranslationPipeline {
  const ImageTranslationPipeline({required this.ocr, required this.detect});

  final OcrStep ocr;
  final DetectStep detect;

  /// Decodes [bytes] and translates it; the result keeps the decoded image
  /// for display and export (the in-app "Dịch ảnh" screen).
  Future<ImageTranslationResult> run({
    required String imagePath,
    required Uint8List bytes,
    required String source,
    required String target,
    required TranslateStep translate,
    void Function(TranslationStage stage)? onStage,
  }) async {
    final codec = await ui.instantiateImageCodec(bytes);
    final image = (await codec.getNextFrame()).image;
    codec.dispose();
    try {
      final rgba = (await image.toByteData(
        format: ui.ImageByteFormat.rawRgba,
      ))!.buffer.asUint8List();
      final regions = await analyze(
        imagePath: imagePath,
        rgba: rgba,
        width: image.width,
        height: image.height,
        source: source,
        target: target,
        translate: translate,
        onStage: onStage,
      );
      return ImageTranslationResult(image: image, regions: regions);
    } catch (_) {
      image.dispose();
      rethrow;
    }
  }

  /// The pipeline on raw RGBA pixels. Used directly by the floating screen
  /// translator, whose background engine gets the pixels from Android and
  /// never decodes an image.
  Future<List<TextRegion>> analyze({
    required String imagePath,
    required Uint8List rgba,
    required int width,
    required int height,
    required String source,
    required String target,
    required TranslateStep translate,
    void Function(TranslationStage stage)? onStage,
    List<ui.Rect> ignore = const [],
  }) async {
    onStage?.call(TranslationStage.reading);
    final size = ui.Size(width.toDouble(), height.toDouble());
    final bounds = ui.Offset.zero & size;
    final blocks = [
      for (final block in await ocr(imagePath, ocrScriptFor(source)))
        if (block.box.intersect(bounds).width > 1 &&
            block.box.intersect(bounds).height > 1 &&
            // e.g. the status and navigation bars of a live screen capture
            !ignore.any((area) => area.contains(block.box.center)))
          OcrBlock(
            text: block.text,
            box: block.box.intersect(bounds),
            lines: [for (final line in block.lines) line.intersect(bounds)],
          ),
    ];

    onStage?.call(TranslationStage.detecting);
    final languages = <String>[
      for (final block in blocks)
        source == autoLanguage ? await detect(block.text) : source,
    ];

    // Group translatable blocks by language; each distinct text is sent once.
    final groups = <String, List<String>>{};
    for (var i = 0; i < blocks.length; i++) {
      if (!shouldTranslate(blocks[i].text, languages[i], target)) continue;
      final texts = groups.putIfAbsent(languages[i], () => []);
      if (!texts.contains(blocks[i].text)) texts.add(blocks[i].text);
    }

    onStage?.call(TranslationStage.translating);
    final translations = <String, Map<String, String>>{};
    for (final MapEntry(key: language, value: texts) in groups.entries) {
      final result = translations[language] = {};
      for (final batch in _batches(texts)) {
        final values = await translate(batch, source: language, target: target);
        for (var i = 0; i < batch.length; i++) {
          result[batch[i]] = values[i];
        }
      }
    }

    onStage?.call(TranslationStage.layout);
    final regions = <TextRegion>[];
    for (var i = 0; i < blocks.length; i++) {
      final block = blocks[i];
      final colors = sampleRegionColors(rgba, width, height, block.box);
      final translation = translations[languages[i]]?[block.text];
      var region = TextRegion(
        id: i,
        sourceText: block.text,
        language: languages[i],
        box: block.box,
        lines: block.lines,
        background: colors.background,
        foreground: colors.foreground,
        translation: translation,
      );
      if (region.translated) {
        final lineHeight = _lineHeight(block);
        // Room the translation may use: the plain background around the
        // text, e.g. the padding of its chat bubble.
        final room = expandToBackground(
          rgba,
          width,
          height,
          block.box,
          colors.background,
          maxGrowX: lineHeight * 1.5,
          maxGrowY: lineHeight * 1.2,
          // Keep a margin from the bubble edge proportional to the text.
          inset: math.max(6, lineHeight * 0.3),
        );
        final fitted = fitText(
          text: translation!,
          box: block.box,
          room: room,
          lineHeight: lineHeight,
          image: size,
        );
        region = region.copyWith(
          fontSize: fitted.fontSize,
          paintBox: fitted.paintBox,
          overflow: fitted.overflow,
        );
      }
      regions.add(region);
    }
    return regions;
  }

  static double _lineHeight(OcrBlock block) {
    if (block.lines.isEmpty) return block.box.height;
    final total = block.lines.fold<double>(0, (sum, line) => sum + line.height);
    return total / block.lines.length;
  }

  static Iterable<List<String>> _batches(List<String> texts) sync* {
    var batch = <String>[];
    var chars = 0;
    for (final text in texts) {
      if (batch.isNotEmpty &&
          (batch.length >= _batchItems || chars + text.length > _batchChars)) {
        yield batch;
        batch = [];
        chars = 0;
      }
      batch.add(text);
      chars += text.length;
    }
    if (batch.isNotEmpty) yield batch;
  }
}
