import 'dart:ui';

/// One block of text OCR found in the image, as image-pixel coordinates.
class OcrBlock {
  const OcrBlock({required this.text, required this.box, required this.lines});

  final String text;
  final Rect box;

  /// Each recognised line; the cover is painted per line so gaps between
  /// lines (and whatever sits there) stay visible.
  final List<Rect> lines;
}

/// A block after translation and layout, ready to paint over the original.
class TextRegion {
  const TextRegion({
    required this.id,
    required this.sourceText,
    required this.language,
    required this.box,
    required this.lines,
    required this.background,
    required this.foreground,
    this.translation,
    this.fontSize = 14,
    this.paintBox,
    this.overflow = false,
  });

  final int id;
  final String sourceText;

  /// Backend language code of the source text.
  final String language;
  final Rect box;
  final List<Rect> lines;
  final Color background;
  final Color foreground;
  final String? translation;

  /// Image-pixel font size chosen so the translation fits [paintBox].
  final double fontSize;

  /// Area painted with [background] and the translation; [box] unless the
  /// translation did not fit at the smallest font size and grew downwards.
  final Rect? paintBox;

  /// True when the translation needed more room than the original text.
  final bool overflow;

  bool get translated => (translation ?? '').trim().isNotEmpty;

  TextRegion copyWith({
    String? translation,
    double? fontSize,
    Rect? paintBox,
    bool? overflow,
  }) => TextRegion(
    id: id,
    sourceText: sourceText,
    language: language,
    box: box,
    lines: lines,
    background: background,
    foreground: foreground,
    translation: translation ?? this.translation,
    fontSize: fontSize ?? this.fontSize,
    paintBox: paintBox ?? this.paintBox,
    overflow: overflow ?? this.overflow,
  );
}

final _letter = RegExp(r'[\p{L}]', unicode: true);

/// Whether a block is worth translating: it has letters, is not already in
/// the target language, and is not a lone symbol or timestamp.
bool shouldTranslate(String text, String language, String target) {
  final trimmed = text.trim();
  if (trimmed.isEmpty || !_letter.hasMatch(trimmed)) return false;
  if (language == target) return false;
  return trimmed.runes
              .where((rune) => _letter.hasMatch(String.fromCharCode(rune)))
              .length >=
          2 ||
      RegExp(r'[㐀-鿿]').hasMatch(trimmed);
}
