// `GET /api/v1/comic/options`: what the creation form and editor may offer.

class ProviderOption {
  const ProviderOption({required this.key, required this.label, this.configured = true, this.allowed = true});

  final String key;
  final String label;
  final bool configured;

  /// False when the account's plan does not include it.
  final bool allowed;

  bool get usable => configured && allowed;

  /// Same suffixes as the web select.
  String get displayLabel => !allowed
      ? '$label — cần gói Creator/Pro'
      : !configured
          ? '$label — chưa cấu hình'
          : label;

  factory ProviderOption.fromJson(Map<String, dynamic> json) => ProviderOption(
        key: json['key']?.toString() ?? '',
        label: json['label']?.toString() ?? json['key']?.toString() ?? '',
        configured: json['configured'] != false,
        allowed: json['allowed'] != false,
      );
}

class TtsVoice {
  const TtsVoice({required this.key, required this.label, this.gender});

  final String key;
  final String label;
  final String? gender;

  factory TtsVoice.fromJson(Map<String, dynamic> json) => TtsVoice(
        key: json['key']?.toString() ?? '',
        label: json['label']?.toString() ?? json['key']?.toString() ?? '',
        gender: json['gender']?.toString(),
      );
}

class TtsProviderOption extends ProviderOption {
  const TtsProviderOption({required super.key, required super.label, super.configured, this.voices = const []});

  final List<TtsVoice> voices;

  /// CIT voices of one gender for a voice select, or every voice when none match (as on the web).
  List<TtsVoice> voicesFor(String gender) {
    final matching = voices.where((voice) => voice.gender == gender).toList();
    return matching.isEmpty ? voices : matching;
  }

  factory TtsProviderOption.fromJson(Map<String, dynamic> json) => TtsProviderOption(
        key: json['key']?.toString() ?? '',
        label: json['label']?.toString() ?? '',
        configured: json['configured'] != false,
        voices: [
          for (final item in (json['voices'] as List? ?? const []))
            if (item is Map) TtsVoice.fromJson(Map<String, dynamic>.from(item)),
        ],
      );
}

class GenrePreset {
  const GenrePreset({required this.key, required this.label, this.style = '', this.worldVi = ''});

  final String key;
  final String label;
  final String style;
  final String worldVi;

  factory GenrePreset.fromJson(Map<String, dynamic> json) => GenrePreset(
        key: json['key']?.toString() ?? '',
        label: json['label']?.toString() ?? '',
        style: json['style']?.toString() ?? '',
        worldVi: json['world_vi']?.toString() ?? '',
      );
}

class ComicOptions {
  const ComicOptions({
    this.styles = const [],
    this.aspectRatios = const ['9:16', '16:9'],
    this.cameraMotions = const [],
    this.languages = const [],
    this.llmProviders = const [],
    this.imageProviders = const [],
    this.animationProviders = const [],
    this.ttsProviders = const [],
    this.shotTypes = const [],
    this.shotMovements = const [],
    this.shotAngles = const [],
    this.maxStoryChars = 0,
    this.genres = const [],
  });

  final List<ProviderOption> styles;
  final List<String> aspectRatios;
  final List<String> cameraMotions;
  final List<ProviderOption> languages;
  final List<ProviderOption> llmProviders;
  final List<ProviderOption> imageProviders;
  final List<ProviderOption> animationProviders;
  final List<TtsProviderOption> ttsProviders;
  final List<String> shotTypes;
  final List<String> shotMovements;
  final List<String> shotAngles;
  final int maxStoryChars;
  final List<GenrePreset> genres;

  TtsProviderOption? tts(String key) => ttsProviders.where((p) => p.key == key).firstOrNull;

  String styleLabel(String? key) => styles.where((s) => s.key == key).firstOrNull?.label ?? key ?? '';

  /// The web's default drawing engine: ComfyUI, then Cloudflare, then Gemini
  /// (when the plan allows it), otherwise Pollinations.
  String get defaultImageProvider {
    ProviderOption? find(String key) => imageProviders.where((p) => p.key == key).firstOrNull;
    if (find('comfyui')?.configured ?? false) return 'comfyui';
    if (find('cloudflare')?.configured ?? false) return 'cloudflare';
    final gemini = find('gemini');
    if (gemini != null && gemini.configured && gemini.allowed) return 'gemini';
    return 'pollinations';
  }

  /// CIT Voice Studio when its app is running, otherwise Edge TTS.
  String get defaultTtsProvider => (tts('cit')?.configured ?? false) ? 'cit' : 'edge';

  factory ComicOptions.fromJson(Map<String, dynamic> json) {
    List<ProviderOption> providers(String key) => [
          for (final item in (json[key] as List? ?? const []))
            if (item is Map) ProviderOption.fromJson(Map<String, dynamic>.from(item)),
        ];
    List<String> strings(String key) => [for (final item in (json[key] as List? ?? const [])) item.toString()];
    return ComicOptions(
      styles: providers('styles'),
      aspectRatios: strings('aspect_ratios'),
      cameraMotions: strings('camera_motions'),
      languages: providers('languages'),
      llmProviders: providers('llm_providers'),
      imageProviders: providers('image_providers'),
      animationProviders: providers('animation_providers'),
      ttsProviders: [
        for (final item in (json['tts_providers'] as List? ?? const []))
          if (item is Map) TtsProviderOption.fromJson(Map<String, dynamic>.from(item)),
      ],
      shotTypes: strings('shot_types'),
      shotMovements: strings('shot_movements'),
      shotAngles: strings('shot_angles'),
      maxStoryChars: (json['max_story_chars'] as num?)?.toInt() ?? 0,
      genres: [
        for (final item in (json['genres'] as List? ?? const []))
          if (item is Map) GenrePreset.fromJson(Map<String, dynamic>.from(item)),
      ],
    );
  }
}
