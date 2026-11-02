import 'package:google_mlkit_language_id/google_mlkit_language_id.dart';
import 'package:google_mlkit_translation/google_mlkit_translation.dart';

import '../../../core/api/api_client.dart';
import '../domain/image_languages.dart';

/// Where the translation runs (design §10).
enum TranslationMode {
  /// ML Kit on the phone: free, private, needs a one-time model download.
  offline,

  /// VidTrans server translators: better wording; only the text is sent.
  online,
}

/// Translates a batch of text blocks of one source language.
abstract interface class BlockTranslator {
  Future<List<String>> translate(
    List<String> texts, {
    required String source,
    required String target,
  });
}

/// Thrown when offline models are missing; the screen asks before downloading.
class ModelsMissing implements Exception {
  const ModelsMissing(this.languages);

  /// Backend codes of the languages whose model must be downloaded.
  final List<String> languages;
}

TranslateLanguage _mlKit(String code) {
  final bcp = imageLanguage(code)?.mlKit ?? code;
  final language = TranslateLanguage.values.where(
    (value) => value.bcpCode == bcp,
  );
  if (language.isEmpty) {
    throw ArgumentError('Ngôn ngữ $code chưa hỗ trợ dịch offline');
  }
  return language.first;
}

/// Google ML Kit on-device translation (about 30 MB per language model).
class OfflineBlockTranslator implements BlockTranslator {
  final _models = OnDeviceTranslatorModelManager();

  Future<List<String>> missingModels(Iterable<String> codes) async {
    final missing = <String>[];
    for (final code in {...codes}) {
      if (!await _models.isModelDownloaded(_mlKit(code).bcpCode)) {
        missing.add(code);
      }
    }
    return missing;
  }

  Future<void> download(
    Iterable<String> codes, {
    required bool wifiOnly,
  }) async {
    for (final code in {...codes}) {
      final ok = await _models.downloadModel(
        _mlKit(code).bcpCode,
        isWifiRequired: wifiOnly,
      );
      if (!ok) {
        throw StateError(
          'Không tải được gói dịch offline cho ${imageLanguage(code)?.label ?? code}',
        );
      }
    }
  }

  @override
  Future<List<String>> translate(
    List<String> texts, {
    required String source,
    required String target,
  }) async {
    final missing = await missingModels([source, target]);
    if (missing.isNotEmpty) throw ModelsMissing(missing);
    final translator = OnDeviceTranslator(
      sourceLanguage: _mlKit(source),
      targetLanguage: _mlKit(target),
    );
    try {
      return [for (final text in texts) await translator.translateText(text)];
    } finally {
      await translator.close();
    }
  }
}

/// `POST /api/v1/text-translations` — sends the recognised text only.
class OnlineBlockTranslator implements BlockTranslator {
  OnlineBlockTranslator(this._api);

  final ApiClient _api;

  @override
  Future<List<String>> translate(
    List<String> texts, {
    required String source,
    required String target,
  }) async {
    final result = await _api.post<Map<String, dynamic>>(
      '/api/v1/text-translations',
      data: {
        'texts': texts,
        'source_language': source,
        'target_language': target,
      },
    );
    final values = (result['translations'] as List? ?? const [])
        .map((value) => value.toString())
        .toList();
    if (values.length != texts.length) {
      throw StateError('Máy chủ trả về thiếu bản dịch');
    }
    return values;
  }
}

/// On-device language identification for "Tự nhận diện".
class BlockLanguageDetector {
  final _identifier = LanguageIdentifier(confidenceThreshold: 0.5);

  /// Backend code of [text]'s language; falls back to a script guess.
  Future<String> detect(String text) async {
    try {
      final code = languageFromMlKit(await _identifier.identifyLanguage(text));
      if (code != null) return code;
    } catch (_) {
      // Identification is best effort; the script guess below still works.
    }
    return guessLanguage(text);
  }

  Future<void> close() => _identifier.close();
}
