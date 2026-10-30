import 'dart:convert';

/// Settings of a translation batch, mirroring the web form `#batchForm`
/// (web/index.html) and `POST /api/v1/batches`. Defaults match the web form.
class TranslationConfig {
  const TranslationConfig({
    this.batchName = 'Batch video mới',
    this.mode = 1,
    this.sourceLanguage = 'zh-CN',
    this.targetLanguage = 'vi',
    this.translationProvider = 'gemini',
    this.subtitleSource = 'speech',
    this.whisperModel = 'medium',
    this.ttsProvider = 'cit',
    this.voiceMode = 'auto',
    this.voiceKey = 'female',
    this.voiceGender = 'female',
    this.voiceStyle = 'auto',
    this.speechRate = 1.0,
    this.originalAudioVolume = 0.60,
    this.musicVolume = 0.20,
    this.keepOriginalAudio = true,
    this.ocrSampleFps = 5,
    this.ocrRoiTop = 0.68,
    this.ocrRoiBottom = 0.96,
    this.subtitle = const SubtitleStyle(),
    this.generateTiktokPost = true,
    this.tiktokMaxSummaryChars = 350,
    this.tiktokHashtagCount = 5,
  });

  final String batchName;

  /// 1 subtitles only, 2 subtitles + dubbing, 3 dubbing + background music.
  final int mode;
  final String sourceLanguage;
  final String targetLanguage;
  final String translationProvider;
  final String subtitleSource;
  final String whisperModel;
  final String ttsProvider;
  final String voiceMode;

  /// Selected voice option: a voice key for providers with a voice list
  /// (elevenlabs, cit), otherwise `female` / `male`.
  final String voiceKey;
  final String voiceGender;
  final String voiceStyle;
  final double speechRate;
  final double originalAudioVolume;
  final double musicVolume;
  final bool keepOriginalAudio;
  final double ocrSampleFps;
  final double ocrRoiTop;
  final double ocrRoiBottom;
  final SubtitleStyle subtitle;
  final bool generateTiktokPost;
  final int tiktokMaxSummaryChars;
  final int tiktokHashtagCount;

  bool get hasVoice => mode == 2 || mode == 3;
  bool get needsMusic => mode == 3;

  /// OCR-based subtitle sources only work for Chinese sources (web rule).
  bool get ocrAllowed => sourceLanguage == 'zh-CN';

  /// Returns an error message, or null when the settings can be submitted.
  String? validate() {
    if (sourceLanguage != 'auto' && sourceLanguage == targetLanguage) {
      return 'Ngôn ngữ nguồn và đầu ra phải khác nhau';
    }
    if (ocrRoiTop >= ocrRoiBottom) return 'Mép trên vùng OCR phải nhỏ hơn mép dưới';
    return null;
  }

  /// Form fields exactly as the web form serializes them: controls the web
  /// disables for the chosen mode are left out, so the server applies the
  /// same defaults for both clients.
  Map<String, String> toFormFields() {
    final subtitleSourceValue = ocrAllowed ? subtitleSource : 'speech';
    final fields = <String, String>{
      'batch_name': batchName.trim().isEmpty ? 'Batch video mới' : batchName.trim(),
      'mode': '$mode',
      'source_language': sourceLanguage,
      'target_language': targetLanguage,
      'translation_provider': translationProvider,
      'subtitle_source': subtitleSourceValue,
      'whisper_model': whisperModel,
      'ocr_sample_fps': _num(ocrSampleFps),
      'ocr_roi_top': _num(ocrRoiTop),
      'ocr_roi_bottom': _num(ocrRoiBottom),
      // The web always sets it from the checkbox, even when the box is disabled.
      'keep_original_audio': '$keepOriginalAudio',
      'generate_tiktok_post': '$generateTiktokPost',
      'tiktok_max_summary_chars': '$tiktokMaxSummaryChars',
      'tiktok_hashtag_count': '$tiktokHashtagCount',
      'auto_publish_tiktok': 'false',
      'subtitle_style': jsonEncode(subtitle.toJson()),
    };
    if (hasVoice) {
      final hasVoiceList = ttsProvider == 'elevenlabs' || ttsProvider == 'cit';
      final isElevenLabs = ttsProvider == 'elevenlabs';
      final female = voiceGender != 'male';
      fields.addAll({
        'tts_provider': ttsProvider,
        'voice_mode': voiceMode,
        'voice_type': voiceGender,
        // The picked voice only fills its own gender; the server picks the other.
        'elevenlabs_female_voice': isElevenLabs && female ? voiceKey : '',
        'elevenlabs_male_voice': isElevenLabs && !female ? voiceKey : '',
        'tts_female_voice': hasVoiceList && female ? voiceKey : '',
        'tts_male_voice': hasVoiceList && !female ? voiceKey : '',
        'voice_style': voiceStyle,
        'speech_rate': _num(speechRate),
        'original_audio_volume': _num(originalAudioVolume),
      });
    }
    if (needsMusic) fields['music_volume'] = _num(musicVolume);
    return fields;
  }

  static String _num(double value) =>
      value == value.roundToDouble() ? value.toInt().toString() : value.toString();

  TranslationConfig copyWith({
    String? batchName,
    int? mode,
    String? sourceLanguage,
    String? targetLanguage,
    String? translationProvider,
    String? subtitleSource,
    String? whisperModel,
    String? ttsProvider,
    String? voiceMode,
    String? voiceKey,
    String? voiceGender,
    String? voiceStyle,
    double? speechRate,
    double? originalAudioVolume,
    double? musicVolume,
    bool? keepOriginalAudio,
    double? ocrSampleFps,
    double? ocrRoiTop,
    double? ocrRoiBottom,
    SubtitleStyle? subtitle,
    bool? generateTiktokPost,
    int? tiktokMaxSummaryChars,
    int? tiktokHashtagCount,
  }) =>
      TranslationConfig(
        batchName: batchName ?? this.batchName,
        mode: mode ?? this.mode,
        sourceLanguage: sourceLanguage ?? this.sourceLanguage,
        targetLanguage: targetLanguage ?? this.targetLanguage,
        translationProvider: translationProvider ?? this.translationProvider,
        subtitleSource: subtitleSource ?? this.subtitleSource,
        whisperModel: whisperModel ?? this.whisperModel,
        ttsProvider: ttsProvider ?? this.ttsProvider,
        voiceMode: voiceMode ?? this.voiceMode,
        voiceKey: voiceKey ?? this.voiceKey,
        voiceGender: voiceGender ?? this.voiceGender,
        voiceStyle: voiceStyle ?? this.voiceStyle,
        speechRate: speechRate ?? this.speechRate,
        originalAudioVolume: originalAudioVolume ?? this.originalAudioVolume,
        musicVolume: musicVolume ?? this.musicVolume,
        keepOriginalAudio: keepOriginalAudio ?? this.keepOriginalAudio,
        ocrSampleFps: ocrSampleFps ?? this.ocrSampleFps,
        ocrRoiTop: ocrRoiTop ?? this.ocrRoiTop,
        ocrRoiBottom: ocrRoiBottom ?? this.ocrRoiBottom,
        subtitle: subtitle ?? this.subtitle,
        generateTiktokPost: generateTiktokPost ?? this.generateTiktokPost,
        tiktokMaxSummaryChars: tiktokMaxSummaryChars ?? this.tiktokMaxSummaryChars,
        tiktokHashtagCount: tiktokHashtagCount ?? this.tiktokHashtagCount,
      );

  /// Saved form state (remembered between sessions). The batch name is not kept.
  Map<String, dynamic> toPrefs() => {
        'mode': mode,
        'source_language': sourceLanguage,
        'target_language': targetLanguage,
        'translation_provider': translationProvider,
        'subtitle_source': subtitleSource,
        'whisper_model': whisperModel,
        'tts_provider': ttsProvider,
        'voice_mode': voiceMode,
        'voice_key': voiceKey,
        'voice_gender': voiceGender,
        'voice_style': voiceStyle,
        'speech_rate': speechRate,
        'original_audio_volume': originalAudioVolume,
        'music_volume': musicVolume,
        'keep_original_audio': keepOriginalAudio,
        'ocr_sample_fps': ocrSampleFps,
        'ocr_roi_top': ocrRoiTop,
        'ocr_roi_bottom': ocrRoiBottom,
        'subtitle': subtitle.toJson(),
        'generate_tiktok_post': generateTiktokPost,
        'tiktok_max_summary_chars': tiktokMaxSummaryChars,
        'tiktok_hashtag_count': tiktokHashtagCount,
      };

  factory TranslationConfig.fromPrefs(Map<String, dynamic> json) {
    const d = TranslationConfig();
    double n(String key, double fallback) => (json[key] as num?)?.toDouble() ?? fallback;
    return TranslationConfig(
      mode: (json['mode'] as num?)?.toInt() ?? d.mode,
      sourceLanguage: json['source_language'] as String? ?? d.sourceLanguage,
      targetLanguage: json['target_language'] as String? ?? d.targetLanguage,
      translationProvider: json['translation_provider'] as String? ?? d.translationProvider,
      subtitleSource: json['subtitle_source'] as String? ?? d.subtitleSource,
      whisperModel: json['whisper_model'] as String? ?? d.whisperModel,
      ttsProvider: json['tts_provider'] as String? ?? d.ttsProvider,
      voiceMode: json['voice_mode'] as String? ?? d.voiceMode,
      voiceKey: json['voice_key'] as String? ?? d.voiceKey,
      voiceGender: json['voice_gender'] as String? ?? d.voiceGender,
      voiceStyle: json['voice_style'] as String? ?? d.voiceStyle,
      speechRate: n('speech_rate', d.speechRate),
      originalAudioVolume: n('original_audio_volume', d.originalAudioVolume),
      musicVolume: n('music_volume', d.musicVolume),
      keepOriginalAudio: json['keep_original_audio'] as bool? ?? d.keepOriginalAudio,
      ocrSampleFps: n('ocr_sample_fps', d.ocrSampleFps),
      ocrRoiTop: n('ocr_roi_top', d.ocrRoiTop),
      ocrRoiBottom: n('ocr_roi_bottom', d.ocrRoiBottom),
      subtitle: json['subtitle'] is Map
          ? SubtitleStyle.fromJson(Map<String, dynamic>.from(json['subtitle'] as Map))
          : d.subtitle,
      generateTiktokPost: json['generate_tiktok_post'] as bool? ?? d.generateTiktokPost,
      tiktokMaxSummaryChars: (json['tiktok_max_summary_chars'] as num?)?.toInt() ?? d.tiktokMaxSummaryChars,
      tiktokHashtagCount: (json['tiktok_hashtag_count'] as num?)?.toInt() ?? d.tiktokHashtagCount,
    );
  }
}

/// `subtitle_style` JSON, built like subtitleStyle() in web/js/app.js.
class SubtitleStyle {
  const SubtitleStyle({
    this.placementMode = 'replace_original',
    this.matchSourceSize = true,
    this.minFontSize = 22,
    this.maxFontSize = 72,
    this.positionGap = 14,
    this.maskOriginal = true,
    this.textColor = '#ffffff',
    this.backgroundColor = '#000000',
    this.backgroundStyle = 'box',
  });

  final String placementMode;
  final bool matchSourceSize;
  final int minFontSize;
  final int maxFontSize;
  final int positionGap;
  final bool maskOriginal;
  final String textColor;
  final String backgroundColor;
  final String backgroundStyle;

  Map<String, dynamic> toJson() => {
        'font_name': 'Noto Sans',
        'font_size': 36,
        'margin_v': 60,
        'placement_mode': placementMode,
        'match_source_size': matchSourceSize,
        'min_font_size': minFontSize,
        'max_font_size': maxFontSize,
        'position_gap': positionGap,
        'mask_original': maskOriginal,
        'text_color_css': textColor,
        'background_color_css': backgroundColor,
        'background_opacity': 0.82,
        'background_style': backgroundStyle,
        'background_box': backgroundStyle == 'box',
      };

  factory SubtitleStyle.fromJson(Map<String, dynamic> json) {
    const d = SubtitleStyle();
    return SubtitleStyle(
      placementMode: json['placement_mode'] as String? ?? d.placementMode,
      matchSourceSize: json['match_source_size'] as bool? ?? d.matchSourceSize,
      minFontSize: (json['min_font_size'] as num?)?.toInt() ?? d.minFontSize,
      maxFontSize: (json['max_font_size'] as num?)?.toInt() ?? d.maxFontSize,
      positionGap: (json['position_gap'] as num?)?.toInt() ?? d.positionGap,
      maskOriginal: json['mask_original'] as bool? ?? d.maskOriginal,
      textColor: json['text_color_css'] as String? ?? d.textColor,
      backgroundColor: json['background_color_css'] as String? ?? d.backgroundColor,
      backgroundStyle: json['background_style'] as String? ?? d.backgroundStyle,
    );
  }

  SubtitleStyle copyWith({
    String? placementMode,
    bool? matchSourceSize,
    int? minFontSize,
    int? maxFontSize,
    int? positionGap,
    bool? maskOriginal,
    String? textColor,
    String? backgroundColor,
    String? backgroundStyle,
  }) =>
      SubtitleStyle(
        placementMode: placementMode ?? this.placementMode,
        matchSourceSize: matchSourceSize ?? this.matchSourceSize,
        minFontSize: minFontSize ?? this.minFontSize,
        maxFontSize: maxFontSize ?? this.maxFontSize,
        positionGap: positionGap ?? this.positionGap,
        maskOriginal: maskOriginal ?? this.maskOriginal,
        textColor: textColor ?? this.textColor,
        backgroundColor: backgroundColor ?? this.backgroundColor,
        backgroundStyle: backgroundStyle ?? this.backgroundStyle,
      );
}
