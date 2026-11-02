/// Languages of image translation and how each engine names them.
///
/// `code` is the backend code (domain/languages.py), `mlKit` the BCP-47 code
/// of ML Kit translation and language identification.
library;

/// OCR model family. Only the Chinese model (which also reads Latin script)
/// and the Latin model are bundled; Japanese/Korean come in a later phase.
enum OcrScript { chinese, latin }

class ImageLanguage {
  const ImageLanguage(this.code, this.label, this.mlKit, this.script);

  final String code;
  final String label;
  final String mlKit;
  final OcrScript script;
}

const autoLanguage = 'auto';

const imageLanguages = <ImageLanguage>[
  ImageLanguage('zh-CN', 'Tiếng Trung', 'zh', OcrScript.chinese),
  ImageLanguage('en', 'Tiếng Anh', 'en', OcrScript.latin),
  ImageLanguage('vi', 'Tiếng Việt', 'vi', OcrScript.latin),
  ImageLanguage('id', 'Tiếng Indonesia', 'id', OcrScript.latin),
  ImageLanguage('es', 'Tiếng Tây Ban Nha', 'es', OcrScript.latin),
];

const imageTargetCodes = ['vi', 'en', 'zh-CN'];

ImageLanguage? imageLanguage(String code) {
  for (final language in imageLanguages) {
    if (language.code == code) return language;
  }
  return null;
}

/// Backend code for an ML Kit language-identification result, or null when
/// the language is undetermined or not supported here.
String? languageFromMlKit(String identified) {
  final base = identified.toLowerCase().split(RegExp('[-_]')).first;
  for (final language in imageLanguages) {
    if (language.mlKit == base) return language.code;
  }
  return null;
}

/// The OCR model for a source choice; auto uses the Chinese model, which
/// also reads Latin text, so mixed screenshots still work.
OcrScript ocrScriptFor(String sourceCode) =>
    imageLanguage(sourceCode)?.script ?? OcrScript.chinese;

final _cjk = RegExp(r'[㐀-鿿豈-﫿]');

/// Cheap guess used when on-device identification is unsure: any Han
/// character means Chinese, otherwise English.
String guessLanguage(String text) => _cjk.hasMatch(text) ? 'zh-CN' : 'en';
