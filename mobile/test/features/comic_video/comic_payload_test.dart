import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/features/comic_video/data/comic_repository.dart';
import 'package:vidtrans_mobile/features/comic_video/domain/comic_forms.dart';
import 'package:vidtrans_mobile/features/comic_video/domain/comic_options.dart';
import 'package:vidtrans_mobile/features/comic_video/domain/story_bible.dart';
import 'package:vidtrans_mobile/features/comic_video/presentation/screens/scene_editor_screen.dart';

void main() {
  group('StoryForm', () {
    test('posts the same fields and defaults as the web "Tạo truyện mới" form', () {
      final fields = StoryForm().toFields();
      expect(fields, {
        'title': 'Truyện mới',
        'genre': '',
        'text': '',
        'video_mode': 'comic',
        'aspect_ratio': '9:16',
        'description': '',
        'story_brief': '',
        'episode_title': '',
        'youtube_title': '',
        'animation_provider': 'camera_2_5d',
        'quality': 'final',
        'fps': '24',
        'motion_strength': '0.45',
        'max_animation_attempts': '2',
        'animation_budget_seconds': '240',
        'style': 'manhua',
        'llm_provider': 'auto',
        'image_provider': 'pollinations',
        'language': 'vi',
        'tts_provider': 'edge',
        'narrator_voice': 'female',
        'cit_narrator_voice': '',
        'cit_female_voice': '',
        'cit_male_voice': '',
        'speech_rate': '1',
        'target_scene_duration': '7',
        'seed': '0',
        'music_volume': '0.15',
        'burn_subtitles': 'true',
        'auto_render': 'true',
      });
      // No max_scenes: the web lets the story decide the number of scenes.
      expect(fields.containsKey('max_scenes'), isFalse);
    });

    test('sends the reviewed bible and CIT voices only when used', () {
      final options = ComicOptions.fromJson({
        'tts_providers': [
          {'key': 'cit', 'label': 'CIT', 'configured': true, 'voices': [
            {'key': 'f1', 'label': 'Nữ', 'gender': 'female'},
            {'key': 'm1', 'label': 'Nam', 'gender': 'male'},
          ]},
        ],
        'styles': [{'key': 'xianxia', 'label': 'Tiên hiệp'}, {'key': 'manhua', 'label': 'Manhua'}],
      });
      final form = StoryForm.withOptions(options)
        ..narratorVoice = 'male'
        ..burnSubtitles = false;
      form.syncCitVoices(options);
      form.selectGenre(const GenrePreset(key: 'tien_hiep', label: 'Tiên hiệp', style: 'xianxia'), options);
      final fields = form.toFields(bible: '{"characters":[]}');
      expect(fields['tts_provider'], 'cit');
      expect(fields['cit_narrator_voice'], 'm1');
      expect(fields['cit_female_voice'], 'f1');
      expect(fields['cit_male_voice'], 'm1');
      expect(fields['genre'], 'tien_hiep');
      expect(fields['style'], 'xianxia');
      expect(fields['burn_subtitles'], 'false');
      expect(fields['bible'], '{"characters":[]}');
      // Picking the same genre again clears it.
      form.selectGenre(const GenrePreset(key: 'tien_hiep', label: 'Tiên hiệp', style: 'xianxia'), options);
      expect(form.genre, '');
    });

    test('bible suggestion reads the story step', () {
      final form = StoryForm(text: 'Đêm hôm đó', storyBrief: 'Thời Đường', genre: 'co_trang');
      expect(form.suggestFields(), {
        'text': 'Đêm hôm đó',
        'story_brief': 'Thời Đường',
        'genre': 'co_trang',
        'language': 'vi',
        'llm_provider': 'auto',
      });
    });
  });

  test('EpisodeForm posts the series and only a non-empty bible', () {
    final form = EpisodeForm(seriesId: 's1', text: 'Ba ngày sau', autoRender: false);
    expect(form.toFields(), {
      'title': '',
      'youtube_title': '',
      'text': 'Ba ngày sau',
      'episode_brief': '',
      'auto_render': 'false',
      'series_id': 's1',
    });
    expect(form.suggestFields(language: 'en', llmProvider: 'gemini'), {
      'text': 'Ba ngày sau',
      'story_brief': '',
      'series_id': 's1',
      'language': 'en',
      'llm_provider': 'gemini',
    });
  });

  group('BibleDraft', () {
    BibleDraft draft() => BibleDraft.fromJson({
          'world': 'ancient China',
          'world_vi': 'Trung Quốc cổ đại',
          'characters': [
            {'id': 'minh', 'name': 'Minh', 'appearance': 'thin scholar', 'appearance_vi': 'thư sinh gầy',
              'status': 'known'},
            {'name': 'Lý Thanh Đình', 'appearance_vi': 'tóc dài', 'status': 'new'},
            {'name': 'Lý Thanh Đình', 'status': 'new'},
            {'name': '   ', 'status': 'new'},
          ],
          'locations': [
            {'name': 'Rừng trúc', 'description': 'bamboo', 'description_vi': 'rừng trúc', 'status': 'new'},
          ],
        });

    test('collect makes unique ids, drops unnamed entries and strips status', () {
      final bible = draft().collect();
      final characters = bible['characters'] as List;
      expect(characters.map((c) => c['id']), ['minh', 'ly_thanh_dinh', 'ly_thanh_dinh_2']);
      expect(characters.every((c) => !(c as Map).containsKey('status')), isTrue);
      expect(characters[1]['species'], 'human');
      expect(characters[1]['appearance'], '');
      expect((bible['locations'] as List).single['id'], 'rung_truc');
      expect(bible['world_vi'], 'Trung Quốc cổ đại');
    });

    test('episodes send only new entries and no world', () {
      final bible = jsonDecode(draft().toJsonString(onlyNew: true, withWorld: false)) as Map<String, dynamic>;
      expect((bible['characters'] as List).map((c) => c['id']), ['ly_thanh_dinh', 'ly_thanh_dinh_2']);
      expect(bible['world'], '');
      expect(bible['world_vi'], '');
    });

    test('a changed Vietnamese look clears its English prompt', () {
      final bible = draft();
      final minh = bible.characters.first;
      BibleDraft.setField(minh, 'appearance_vi', 'thư sinh gầy');
      expect(minh['appearance'], 'thin scholar');
      BibleDraft.setField(minh, 'appearance_vi', 'thư sinh cao');
      expect(minh['appearance'], '');
      bible.setWorldVi('Thế giới tu tiên');
      expect(bible.world, '');
      bible.keepOnlyNew();
      expect(bible.characters.length, 3);
      expect(bible.newCharacters, 3);
    });

    test('asciiId folds Vietnamese like the web', () {
      expect(asciiId('Đường Tăng', 'x'), 'duong_tang');
      expect(asciiId('!!!', 'place'), 'place');
      expect(asciiId('a' * 60, 'x').length, 40);
    });
  });

  group('editor payloads', () {
    test('scene patch parses dialogue and cast', () {
      expect(
        scenePatch(
          narration: 'Đêm',
          dialogue: 'minh: Ai đó?',
          characters: 'minh, lan',
          visualPrompt: 'night',
          negativePrompt: '',
          cameraMotion: 'pan_left',
          locationId: '',
        ),
        {
          'narration': 'Đêm',
          'dialogue': [{'character': 'minh', 'text': 'Ai đó?'}],
          'characters': ['minh', 'lan'],
          'visual_prompt': 'night',
          'negative_prompt': '',
          'camera_motion': 'pan_left',
          'location_id': '',
        },
      );
      // Without planned places the field is not sent.
      expect(
        scenePatch(narration: '', dialogue: '', characters: '', visualPrompt: 'x', negativePrompt: '',
                cameraMotion: 'static')
            .containsKey('location_id'),
        isFalse,
      );
    });

    test('shot patch nests camera and animation', () {
      final patch = shotPatch(
        narration: 'n',
        dialogue: '',
        motionPrompt: 'walks',
        keyframePrompt: 'Minh',
        negativePrompt: '',
        characters: 'minh',
        durationSeconds: 4.5,
        shotType: 'close_up',
        movement: 'push_in',
        angle: 'high',
        mode: 'image_to_video',
        motionStrength: 0.6,
      );
      expect(patch['camera'], {'shot_type': 'close_up', 'movement': 'push_in', 'angle': 'high'});
      expect(patch['animation'], {'mode': 'image_to_video', 'motion_strength': 0.6});
      expect(patch['duration_seconds'], 4.5);
    });

    test('render bodies match the web buttons', () {
      expect(RenderRequest.render(), {'images_only': false, 'reanalyze': false});
      expect(RenderRequest.trial(), {'images_only': true, 'reanalyze': false, 'preview_scenes': 3});
      expect(RenderRequest.imagesOnly(), {'images_only': true, 'reanalyze': false});
      expect(RenderRequest.reanalyze(), {'images_only': false, 'reanalyze': true});
      expect(RenderRequest.shot('2.1'), {'shot': '2.1'});
      expect(const ScenePage(page: 2, limit: 10).query, {'scene_page': 2, 'scene_limit': 10});
    });
  });
}
