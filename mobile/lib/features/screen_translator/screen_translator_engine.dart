import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

import '../../core/api/api_client.dart';
import '../../core/config/env.dart';
import '../../core/errors/api_exception.dart';
import '../../core/storage/token_storage.dart';
import '../image_translation/data/block_translators.dart';
import '../image_translation/data/ocr_service.dart';
import '../image_translation/domain/image_translation_pipeline.dart';
import '../image_translation/domain/text_region.dart';

/// Channel between the Android ScreenTranslatorService and this engine.
const engineChannel = MethodChannel('vidtrans/screen_translator_engine');

/// Entry point of the headless Flutter engine the floating screen translator
/// (phase 2) starts in its foreground service. It reuses the phase 1
/// pipeline (OCR → language → translation → colours → layout) on the pixels
/// Android captured and returns what to paint; Android draws the overlay.
@pragma('vm:entry-point')
Future<void> screenTranslatorMain() async {
  WidgetsFlutterBinding.ensureInitialized();
  final engine = ScreenTranslatorEngine.create();
  engineChannel.setMethodCallHandler(engine.handle);
  await engineChannel.invokeMethod<void>('ready');
}

/// Remembers recent translations so translating the same screen again (or
/// a chat that only gained one message) costs no new model or server call.
class CachedBlockTranslator implements BlockTranslator {
  CachedBlockTranslator(this.inner, {this.capacity = 500});

  final BlockTranslator inner;
  final int capacity;
  final _cache = <String, String>{}; // insertion-ordered: LRU

  int hits = 0;

  @override
  Future<List<String>> translate(
    List<String> texts, {
    required String source,
    required String target,
  }) async {
    String key(String text) => '$source\u0000$target\u0000$text';
    final missing = [
      for (final text in texts)
        if (!_cache.containsKey(key(text))) text,
    ];
    hits += texts.length - missing.length;
    if (missing.isNotEmpty) {
      final values = await inner.translate(
        missing,
        source: source,
        target: target,
      );
      for (var i = 0; i < missing.length; i++) {
        if (values[i].trim().isNotEmpty) _cache[key(missing[i])] = values[i];
      }
      while (_cache.length > capacity) {
        _cache.remove(_cache.keys.first);
      }
    }
    return [
      for (final text in texts)
        // Re-insert to keep recently used entries at the end (LRU).
        _cache.containsKey(key(text))
            ? (_cache[key(text)] = _cache.remove(key(text))!)
            : '',
    ];
  }
}

class ScreenTranslatorEngine {
  ScreenTranslatorEngine({
    required this.pipeline,
    required this.offline,
    required this.online,
  });

  factory ScreenTranslatorEngine.create() {
    final ocr = OcrService();
    final detector = BlockLanguageDetector();
    return ScreenTranslatorEngine(
      pipeline: ImageTranslationPipeline(
        ocr: ocr.recognize,
        detect: detector.detect,
      ),
      offline: CachedBlockTranslator(OfflineBlockTranslator()),
      online: CachedBlockTranslator(
        OnlineBlockTranslator(
          // The service has no UI: an expired session just fails the call;
          // the app asks the user to sign in again next time it opens.
          ApiClient(
            baseUrl: Env.apiBase,
            tokens: TokenStorage(),
            onSessionExpired: () {},
          ),
        ),
      ),
    );
  }

  final ImageTranslationPipeline pipeline;
  final BlockTranslator offline;
  final BlockTranslator online;

  Future<Object?> handle(MethodCall call) async {
    if (call.method == 'translateTexts') {
      final args = Map<String, Object?>.from(call.arguments as Map);
      return translateTexts(
        texts: [
          for (final text in (args['texts'] as List? ?? const [])) '$text',
        ],
        source: args['source'] as String? ?? 'auto',
        target: args['target'] as String? ?? 'vi',
        mode: args['mode'] as String? ?? TranslationMode.online.name,
      );
    }
    if (call.method != 'translate') throw MissingPluginException(call.method);
    final args = Map<String, Object?>.from(call.arguments as Map);
    return translate(
      imagePath: args['imagePath']! as String,
      rgba: args['rgba']! as Uint8List,
      width: args['width']! as int,
      height: args['height']! as int,
      source: args['source'] as String? ?? 'auto',
      target: args['target'] as String? ?? 'vi',
      mode: args['mode'] as String? ?? TranslationMode.online.name,
      ignore: [
        for (final area in (args['ignore'] as List? ?? const []))
          ?_rectOf(area),
      ],
      clip: _rectOf(args['clip']),
      ocrOffsetY: (args['ocrOffsetY'] as num?)?.toDouble() ?? 0,
    );
  }

  BlockTranslator _translatorFor(String mode) => switch (mode) {
    _ when mode == TranslationMode.online.name => online,
    // On-device test of capture and overlay without models or a server
    // (integration_test/screen_translator_test.dart); not in release builds.
    'debug-echo' when kDebugMode => const _EchoTranslator(),
    _ => offline,
  };

  /// Accessibility mode: the app's own text, read from its view tree (exact
  /// text and positions, no OCR). Returns `{translations: [...]}` aligned with
  /// [texts] — null where the text needs no translation (already in the
  /// target language, numbers, a time) — or `{error, message}`.
  Future<Map<String, Object?>> translateTexts({
    required List<String> texts,
    required String source,
    required String target,
    required String mode,
  }) async {
    final translator = _translatorFor(mode);
    final languages = <String>[
      for (final text in texts)
        source == 'auto' ? await pipeline.detect(text) : source,
    ];
    final groups = <String, List<String>>{};
    for (var i = 0; i < texts.length; i++) {
      if (!shouldTranslate(texts[i], languages[i], target)) continue;
      final group = groups.putIfAbsent(languages[i], () => []);
      if (!group.contains(texts[i])) group.add(texts[i]);
    }
    final missing = <String>{};
    final result = <String, Map<String, String>>{};
    try {
      for (final MapEntry(key: language, value: group) in groups.entries) {
        try {
          final values = await translator.translate(
            group,
            source: language,
            target: target,
          );
          result[language] = {
            for (var i = 0; i < group.length; i++) group[i]: values[i],
          };
        } on ModelsMissing catch (error) {
          missing.addAll(error.languages);
        }
      }
    } catch (error) {
      return {'error': 'failed', 'message': errorMessage(error)};
    }
    if (result.isEmpty && missing.isNotEmpty) {
      return {'error': 'models_missing', 'languages': missing.toList()};
    }
    return {
      'translations': [
        for (var i = 0; i < texts.length; i++)
          (result[languages[i]]?[texts[i]]?.trim().isNotEmpty ?? false)
              ? result[languages[i]]![texts[i]]
              : null,
      ],
    };
  }

  /// Returns `{found, translated, regions: [...]}` or `{error, message,
  /// languages?}`; never throws, so the service always gets an answer.
  Future<Map<String, Object?>> translate({
    required String imagePath,
    required Uint8List rgba,
    required int width,
    required int height,
    required String source,
    required String target,
    required String mode,
    List<Rect> ignore = const [],
    Rect? clip,
    double ocrOffsetY = 0,
  }) async {
    final translator = _translatorFor(mode);
    final missingModels = <String>{};
    try {
      final regions = await pipeline.analyze(
        imagePath: imagePath,
        rgba: rgba,
        width: width,
        height: height,
        source: source,
        target: target,
        ignore: ignore,
        clip: clip,
        ocrOffsetY: ocrOffsetY,
        translate: (texts, {required source, required target}) async {
          try {
            return await translator.translate(
              texts,
              source: source,
              target: target,
            );
          } on ModelsMissing catch (missing) {
            // Leave this language untranslated; other languages still work.
            missingModels.addAll(missing.languages);
            return List.filled(texts.length, '');
          }
        },
      );
      final translated = regions.where((region) => region.translated).toList();
      if (translated.isEmpty && missingModels.isNotEmpty) {
        return {'error': 'models_missing', 'languages': missingModels.toList()};
      }
      return {
        'found': regions.length,
        'translated': translated.length,
        'missingModels': missingModels.toList(),
        'regions': [for (final region in translated) encodeRegion(region)],
      };
    } catch (error) {
      return {'error': 'failed', 'message': errorMessage(error)};
    } finally {
      try {
        await File(imagePath).delete();
      } catch (_) {}
    }
  }
}

Rect? _rectOf(Object? value) => value is List && value.length == 4
    ? Rect.fromLTRB(
        (value[0] as num).toDouble(),
        (value[1] as num).toDouble(),
        (value[2] as num).toDouble(),
        (value[3] as num).toDouble(),
      )
    : null;

List<double> _rect(Rect rect) => [rect.left, rect.top, rect.right, rect.bottom];

/// What Android needs to paint one translated block (image pixels).
Map<String, Object?> encodeRegion(TextRegion region) => {
  'text': region.translation,
  'paint': _rect(region.paintBox ?? region.box),
  'lines': [for (final line in region.lines) _rect(line)],
  'size': region.fontSize,
  'bg': region.background.toARGB32(),
  'fg': region.foreground.toARGB32(),
};

class _EchoTranslator implements BlockTranslator {
  const _EchoTranslator();

  @override
  Future<List<String>> translate(
    List<String> texts, {
    required String source,
    required String target,
  }) async => [
    for (final text in texts)
      'Bản dịch thử ($source→$target): ${text.length} ký tự',
  ];
}
