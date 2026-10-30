import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/features/comic_video/domain/comic_models.dart';
import 'package:vidtrans_mobile/features/comic_video/domain/comic_options.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job.dart';

void main() {
  group('ComicProject.fromJson', () {
    final payload = {
      'job': {
        'job_id': 'ab12cd34',
        'status': 'completed',
        'step': 'completed',
        'progress': 1.0,
        'video_url': '/api/v1/jobs/ab12cd34/artifacts/video',
        'comic': {'title': 'Tập 2', 'series_id': 's1', 'series_title': 'Rừng trúc', 'episode_number': 2,
          'video_mode': 'animated', 'scenes': 3, 'shots': 5, 'aspect_ratio': '9:16'},
      },
      'settings': {'aspect_ratio': '9:16'},
      'storyboard': {
        'revision': 4,
        'mode': 'animated',
        'analysis_method': 'gemini',
        'characters': [
          {'id': 'minh', 'name': 'Minh', 'gender': 'male', 'appearance_vi': 'tóc búi', 'approved_reference': 'minh-1.png'},
        ],
        'locations': [{'id': 'rung_truc', 'name': 'Rừng trúc', 'description_vi': 'sương mờ'}],
        'scenes': [
          {
            'scene_index': 1,
            'visual_prompt': 'bamboo forest',
            'narration': 'Đêm hôm đó…',
            'dialogue': [{'character': 'minh', 'text': 'Ai đó?'}],
            'location_id': 'rung_truc',
            'shots': [
              {
                'shot_index': 1,
                'keyframe_prompt': 'Minh walks',
                'camera': {'shot_type': 'wide', 'movement': 'push_in', 'angle': 'low'},
                'animation': {'mode': 'camera_2_5d', 'motion_strength': 0.3, 'take': 2},
              },
            ],
          },
        ],
        'warnings': ['Cảnh 1 thiếu nhân vật'],
      },
      'scenes_pagination': {'page': 1, 'limit': 6, 'total_scenes': 3, 'total_pages': 1, 'total_shots': 5},
      'rendered_revision': 3,
      'media': {
        'scenes': {'1': '/m/images/a.png'},
        'uploads': {'1': '/m/uploads/b.png'},
        'keyframes': {'1.1': '/m/keyframes/k.png'},
        'previews': {'1.1': '/m/clips/p.mp4'},
        'animations': {'1.1': '/m/animations/x.mp4'},
        'place_references_supported': true,
      },
      'shots': {'1.1': {'provider': 'camera_2_5d', 'attempts': 1}},
    };

    test('parses the storyboard, media and job summary', () {
      final project = ComicProject.fromJson(payload);
      final storyboard = project.storyboard!;
      expect(storyboard.animated, isTrue);
      expect(storyboard.character('minh')!.approvedReference, 'minh-1.png');
      expect(storyboard.location('rung_truc')!.descriptionVi, 'sương mờ');
      final shot = storyboard.scenes.single.shots.single;
      expect((shot.shotType, shot.movement, shot.angle), ('wide', 'push_in', 'low'));
      expect((shot.animationMode, shot.motionStrength, shot.take), ('camera_2_5d', 0.3, 2));
      expect(storyboard.scenes.single.dialogue.single.text, 'Ai đó?');
      // An uploaded picture wins over the drawn one; a preview over the animation.
      expect(project.media.sceneImage(1), '/m/uploads/b.png');
      expect(project.media.shotClip('1.1'), '/m/clips/p.mp4');
      expect(project.media.placeReferencesSupported, isTrue);
      expect(project.stale, isTrue);
      expect(project.pagination.totalShots, 5);
      final comic = ComicJobInfo.of(project.job);
      expect(comic.episodeTitle(), 'Tập 2');
      expect(comic.meta(), 'Hoạt hình · 3 cảnh · 5 shot · 9:16');
    });

    test('comic progress is a fraction, unlike translation jobs', () {
      expect(comicProgress(Job.fromJson({'job_id': 'x', 'progress': 0.42})), 0.42);
      expect(comicProgress(Job.fromJson({'job_id': 'x'})), 0);
    });
  });

  test('dialogue round-trips through the "id: lời" text format', () {
    final lines = parseDialogue('minh: Ai đó?\n\n lan :  Là ta: Lan ');
    expect([for (final l in lines) l.toJson()], [
      {'character': 'minh', 'text': 'Ai đó?'},
      {'character': 'lan', 'text': 'Là ta: Lan'},
    ]);
    expect(dialogueText(lines), 'minh: Ai đó?\nlan: Là ta: Lan');
    expect(() => parseDialogue('không có dấu hai chấm'), throwsFormatException);
    expect(splitIds(' minh, ,lan '), ['minh', 'lan']);
  });

  test('series detail sorts episodes and reads the visual plan', () {
    final series = SeriesDetail.fromJson({
      'series_id': 's1',
      'title': 'Rừng trúc',
      'episodes': 2,
      'characters': [{'id': 'minh', 'name': 'Minh', 'reference_url': '/r/minh.png'}],
      'settings': {'story_brief': 'Thời Đường', 'video_mode': 'comic'},
      'world_vi': 'Trung Quốc cổ đại',
      'locations': [],
      'episode_list': [
        {'job_id': 'b', 'status': 'processing', 'episode_number': 2},
        {'job_id': 'a', 'status': 'completed', 'episode_number': 1},
      ],
      'next_episode_number': 3,
    });
    expect(series.episodes.map((job) => job.id), ['a', 'b']);
    expect(series.anyActive, isTrue);
    expect(series.storyBrief, 'Thời Đường');
    expect(series.characters.single.referenceUrl, '/r/minh.png');
    expect(series.summary.characters, 1);
    expect(series.nextEpisodeNumber, 3);
  });

  group('ComicOptions defaults', () {
    ComicOptions options({bool comfy = false, bool cloudflare = false, bool geminiAllowed = true, bool cit = false}) =>
        ComicOptions.fromJson({
          'image_providers': [
            {'key': 'comfyui', 'label': 'ComfyUI', 'configured': comfy},
            {'key': 'cloudflare', 'label': 'Cloudflare', 'configured': cloudflare},
            {'key': 'gemini', 'label': 'Gemini', 'configured': true, 'allowed': geminiAllowed},
            {'key': 'pollinations', 'label': 'Pollinations', 'configured': true},
          ],
          'tts_providers': [
            {'key': 'edge', 'label': 'Edge', 'configured': true, 'voices': []},
            {'key': 'cit', 'label': 'CIT', 'configured': cit, 'voices': [
              {'key': 'f1', 'label': 'Nữ 1', 'gender': 'female'},
              {'key': 'm1', 'label': 'Nam 1', 'gender': 'male'},
            ]},
          ],
        });

    test('picks the same drawing engine as the web', () {
      expect(options(comfy: true).defaultImageProvider, 'comfyui');
      expect(options(cloudflare: true).defaultImageProvider, 'cloudflare');
      expect(options().defaultImageProvider, 'gemini');
      expect(options(geminiAllowed: false).defaultImageProvider, 'pollinations');
    });

    test('prefers CIT voices when the app is running', () {
      expect(options().defaultTtsProvider, 'edge');
      expect(options(cit: true).defaultTtsProvider, 'cit');
      expect(options(cit: true).tts('cit')!.voicesFor('male').single.key, 'm1');
    });

    test('labels locked and unconfigured providers like the web select', () {
      final gemini = ProviderOption.fromJson({'key': 'gemini', 'label': 'Gemini', 'allowed': false});
      expect(gemini.displayLabel, 'Gemini — cần gói Creator/Pro');
      expect(gemini.usable, isFalse);
      expect(ProviderOption.fromJson({'key': 'x', 'label': 'X', 'configured': false}).displayLabel,
          'X — chưa cấu hình');
    });
  });
}
