import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/features/video_translation/domain/options.dart';
import 'package:vidtrans_mobile/features/video_translation/domain/translation_config.dart';

void main() {
  group('toFormFields matches the web form', () {
    test('mode 1 leaves out the voice and music controls the web disables', () {
      final fields = const TranslationConfig().toFormFields();
      expect(fields, containsPair('mode', '1'));
      expect(fields, containsPair('batch_name', 'Batch video mới'));
      expect(fields, containsPair('source_language', 'zh-CN'));
      expect(fields, containsPair('target_language', 'vi'));
      expect(fields, containsPair('translation_provider', 'gemini'));
      expect(fields, containsPair('subtitle_source', 'speech'));
      expect(fields, containsPair('whisper_model', 'medium'));
      expect(fields, containsPair('ocr_sample_fps', '5'));
      expect(fields, containsPair('ocr_roi_top', '0.68'));
      expect(fields, containsPair('ocr_roi_bottom', '0.96'));
      expect(fields, containsPair('keep_original_audio', 'true'));
      expect(fields, containsPair('generate_tiktok_post', 'true'));
      expect(fields, containsPair('tiktok_max_summary_chars', '350'));
      expect(fields, containsPair('tiktok_hashtag_count', '5'));
      expect(fields, containsPair('auto_publish_tiktok', 'false'));
      for (final key in [
        'tts_provider', 'voice_mode', 'voice_type', 'voice_style', 'speech_rate',
        'original_audio_volume', 'music_volume', 'tts_female_voice', 'elevenlabs_female_voice',
      ]) {
        expect(fields.containsKey(key), isFalse, reason: key);
      }
    });

    test('subtitle_style is the same JSON as subtitleStyle() on the web', () {
      final style = jsonDecode(const TranslationConfig().toFormFields()['subtitle_style']!);
      expect(style, {
        'font_name': 'Noto Sans',
        'font_size': 36,
        'margin_v': 60,
        'placement_mode': 'replace_original',
        'match_source_size': true,
        'min_font_size': 22,
        'max_font_size': 72,
        'position_gap': 14,
        'mask_original': true,
        'text_color_css': '#ffffff',
        'background_color_css': '#000000',
        'background_opacity': 0.82,
        'background_style': 'box',
        'background_box': true,
      });
    });

    test('mode 2 with a CIT voice fills only the picked gender', () {
      final fields = const TranslationConfig(mode: 2, ttsProvider: 'cit', voiceKey: 'ngoc-huyen', voiceGender: 'female')
          .toFormFields();
      expect(fields, containsPair('tts_provider', 'cit'));
      expect(fields, containsPair('voice_type', 'female'));
      expect(fields, containsPair('tts_female_voice', 'ngoc-huyen'));
      expect(fields, containsPair('tts_male_voice', ''));
      expect(fields, containsPair('elevenlabs_female_voice', ''));
      expect(fields, containsPair('speech_rate', '1'));
      expect(fields, containsPair('original_audio_volume', '0.6'));
      expect(fields.containsKey('music_volume'), isFalse);
    });

    test('ElevenLabs male voice fills both ElevenLabs and generic male fields', () {
      final fields =
          const TranslationConfig(mode: 3, ttsProvider: 'elevenlabs', voiceKey: 'adam', voiceGender: 'male')
              .toFormFields();
      expect(fields, containsPair('voice_type', 'male'));
      expect(fields, containsPair('elevenlabs_male_voice', 'adam'));
      expect(fields, containsPair('tts_male_voice', 'adam'));
      expect(fields, containsPair('elevenlabs_female_voice', ''));
      expect(fields, containsPair('music_volume', '0.2'));
    });

    test('Edge sends only the gender, no voice keys', () {
      final fields = const TranslationConfig(mode: 2, ttsProvider: 'edge', voiceKey: 'male', voiceGender: 'male')
          .toFormFields();
      expect(fields, containsPair('voice_type', 'male'));
      expect(fields, containsPair('tts_male_voice', ''));
      expect(fields, containsPair('elevenlabs_male_voice', ''));
    });

    test('OCR sources fall back to speech when the source is not Chinese', () {
      final fields = const TranslationConfig(sourceLanguage: 'en', subtitleSource: 'burned').toFormFields();
      expect(fields, containsPair('subtitle_source', 'speech'));
      expect(const TranslationConfig(subtitleSource: 'burned').toFormFields(), containsPair('subtitle_source', 'burned'));
    });
  });

  test('validate rejects identical source and target languages', () {
    expect(const TranslationConfig(sourceLanguage: 'vi').validate(), isNotNull);
    expect(const TranslationConfig(sourceLanguage: 'auto').validate(), isNull);
  });

  test('settings survive a prefs round trip', () {
    const config = TranslationConfig(
      mode: 3,
      ttsProvider: 'elevenlabs',
      voiceKey: 'adam',
      voiceGender: 'male',
      speechRate: 1.25,
      subtitle: SubtitleStyle(placementMode: 'bottom_safe', textColor: '#ffeb3b'),
    );
    final restored = TranslationConfig.fromPrefs(jsonDecode(jsonEncode(config.toPrefs())) as Map<String, dynamic>);
    expect(restored.toFormFields()..remove('batch_name'), config.toFormFields()..remove('batch_name'));
  });

  test('extractLinks finds each link once', () {
    final links = extractLinks('xem https://v.douyin.com/abc/ và\nhttps://www.tiktok.com/@a/video/1 https://v.douyin.com/abc/');
    expect(links, ['https://v.douyin.com/abc/', 'https://www.tiktok.com/@a/video/1']);
  });
}
