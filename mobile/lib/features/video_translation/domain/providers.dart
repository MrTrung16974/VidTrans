/// `GET /api/v1/translation-providers` item.
class TranslationProviderInfo {
  const TranslationProviderInfo({
    required this.key,
    required this.label,
    required this.configured,
    required this.allowed,
    this.description = '',
  });

  final String key;
  final String label;
  final bool configured;

  /// False when the plan does not include it (server-enforced).
  final bool allowed;
  final String description;

  bool get usable => configured && allowed;

  String get statusNote {
    if (!allowed) return 'cần gói Creator/Pro';
    if (!configured) return const {'translategemma', 'qwen3'}.contains(key) ? 'chưa chạy Ollama' : 'chưa cấu hình key';
    return '';
  }

  factory TranslationProviderInfo.fromJson(Map<String, dynamic> json) => TranslationProviderInfo(
        key: json['key'] as String,
        label: json['label'] as String? ?? json['key'] as String,
        configured: json['configured'] == true,
        allowed: json['allowed'] != false,
        description: json['description'] as String? ?? '',
      );

  static List<TranslationProviderInfo> listFromJson(Map<String, dynamic> json) => [
        for (final item in json['providers'] as List? ?? const [])
          TranslationProviderInfo.fromJson(Map<String, dynamic>.from(item as Map)),
      ];
}

class TtsVoice {
  const TtsVoice({required this.key, required this.gender, required this.label});

  final String key;
  final String gender;
  final String label;

  String get displayLabel => '${gender == 'male' ? 'Nam' : 'Nữ'} — $label';

  factory TtsVoice.fromJson(Map<String, dynamic> json) => TtsVoice(
        key: json['key'].toString(),
        gender: json['gender'] as String? ?? 'female',
        label: json['label'] as String? ?? json['key'].toString(),
      );
}

/// `GET /api/v1/tts-providers` item.
class TtsProviderInfo {
  const TtsProviderInfo({
    required this.key,
    required this.label,
    required this.configured,
    required this.allowed,
    this.freeNote = '',
    this.femaleVoiceLabel = 'Giọng nữ',
    this.maleVoiceLabel = 'Giọng nam',
    this.supportsPitchMatching = true,
    this.voices = const [],
  });

  final String key;
  final String label;
  final bool configured;
  final bool allowed;
  final String freeNote;
  final String femaleVoiceLabel;
  final String maleVoiceLabel;
  final bool supportsPitchMatching;
  final List<TtsVoice> voices;

  bool get usable => configured && allowed;

  String get statusNote {
    if (!allowed) return 'cần gói Creator/Pro';
    if (!configured) return key == 'cit' ? 'chưa mở ứng dụng' : 'chưa cấu hình key';
    return '';
  }

  /// Voices offered in the picker, as renderProviderVoices() in the web app:
  /// ElevenLabs and CIT list named voices, the others a female/male choice.
  List<TtsVoice> get voiceChoices => (key == 'elevenlabs' || key == 'cit')
      ? voices
      : [
          TtsVoice(key: 'female', gender: 'female', label: femaleVoiceLabel),
          TtsVoice(key: 'male', gender: 'male', label: maleVoiceLabel),
        ];

  factory TtsProviderInfo.fromJson(Map<String, dynamic> json) => TtsProviderInfo(
        key: json['key'] as String,
        label: json['label'] as String? ?? json['key'] as String,
        configured: json['configured'] == true,
        allowed: json['allowed'] != false,
        freeNote: json['free_note'] as String? ?? '',
        femaleVoiceLabel: json['female_voice_label'] as String? ?? 'Giọng nữ',
        maleVoiceLabel: json['male_voice_label'] as String? ?? 'Giọng nam',
        supportsPitchMatching: json['supports_pitch_matching'] != false,
        voices: [
          for (final voice in json['voices'] as List? ?? const [])
            TtsVoice.fromJson(Map<String, dynamic>.from(voice as Map)),
        ],
      );

  static List<TtsProviderInfo> listFromJson(Map<String, dynamic> json) => [
        for (final item in json['providers'] as List? ?? const [])
          TtsProviderInfo.fromJson(Map<String, dynamic>.from(item as Map)),
      ];
}

/// Picks a usable provider when the remembered one is locked, with the same
/// fallbacks as the web form.
String pickTranslationProvider(String current, List<TranslationProviderInfo> providers) {
  final byKey = {for (final p in providers) p.key: p};
  if (byKey[current]?.usable ?? false) return current;
  for (final key in const ['gemini', 'translategemma']) {
    if (byKey[key]?.usable ?? false) return key;
  }
  return 'google';
}

String pickTtsProvider(String current, List<TtsProviderInfo> providers) {
  final byKey = {for (final p in providers) p.key: p};
  return (byKey[current]?.usable ?? false) ? current : 'edge';
}
