import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/features/video_translation/data/translation_repository.dart';
import 'package:vidtrans_mobile/features/video_translation/domain/providers.dart';

void main() {
  test('parses /api/v1/tts-providers and builds the voice picker like the web', () {
    final providers = TtsProviderInfo.listFromJson({
      'providers': [
        {
          'key': 'edge',
          'label': 'Edge TTS',
          'configured': true,
          'free_note': 'Miễn phí',
          'female_voice_label': 'Hoài My',
          'male_voice_label': 'Nam Minh',
          'supports_pitch_matching': true,
          'voices': [],
          'requires_paid_plan': false,
          'allowed': true,
        },
        {
          'key': 'elevenlabs',
          'label': 'ElevenLabs',
          'configured': true,
          'free_note': '',
          'supports_pitch_matching': false,
          'voices': [
            {'key': 'adam', 'gender': 'male', 'label': 'Adam'},
          ],
          'requires_paid_plan': true,
          'allowed': false,
        },
        {'key': 'cit', 'label': 'CIT', 'configured': false, 'free_note': '', 'voices': []},
      ],
    });
    final edge = providers[0];
    expect(edge.usable, isTrue);
    expect(edge.voiceChoices.map((v) => v.displayLabel), ['Nữ — Hoài My', 'Nam — Nam Minh']);
    final eleven = providers[1];
    expect(eleven.usable, isFalse);
    expect(eleven.statusNote, 'cần gói Creator/Pro');
    expect(eleven.supportsPitchMatching, isFalse);
    expect(eleven.voiceChoices.single.key, 'adam');
    expect(providers[2].statusNote, 'chưa mở ứng dụng');
    expect(pickTtsProvider('elevenlabs', providers), 'edge');
    expect(pickTtsProvider('cit', providers), 'edge');
  });

  test('parses /api/v1/translation-providers with the web fallback order', () {
    final providers = TranslationProviderInfo.listFromJson({
      'providers': [
        {'key': 'google', 'label': 'Google Translate', 'configured': true, 'free_tier': true, 'description': 'd', 'allowed': true},
        {'key': 'gemini', 'label': 'Gemini AI', 'configured': true, 'free_tier': true, 'description': '', 'allowed': false},
        {'key': 'translategemma', 'label': 'TranslateGemma', 'configured': true, 'free_tier': true, 'description': '', 'allowed': true},
        {'key': 'qwen3', 'label': 'Qwen3', 'configured': false, 'free_tier': true, 'description': '', 'allowed': true},
      ],
    });
    expect(providers.first.description, 'd');
    expect(providers[3].statusNote, 'chưa chạy Ollama');
    expect(pickTranslationProvider('gemini', providers), 'translategemma');
    expect(pickTranslationProvider('google', providers), 'google');
    expect(pickTranslationProvider('gemini', const []), 'google');
  });

  test('CreatedBatch reads first-time and replayed responses', () {
    final batch = CreatedBatch.fromJson({
      'batch_id': 'b1',
      'name': 'Batch',
      'total_jobs': 2,
      'jobs': [
        {'job_id': 'j1', 'filename': 'a.mp4', 'status': 'queued'},
        {'job_id': 'j2', 'filename': 'b.mp4', 'status': 'queued'},
      ],
      'duplicate': true,
    });
    expect(batch.jobIds, ['j1', 'j2']);
    expect(batch.duplicate, isTrue);
  });
}
