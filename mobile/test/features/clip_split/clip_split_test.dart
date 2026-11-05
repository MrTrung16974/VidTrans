import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/app/theme/app_theme.dart';
import 'package:vidtrans_mobile/core/auth/account.dart';
import 'package:vidtrans_mobile/core/auth/session_controller.dart';
import 'package:vidtrans_mobile/features/clip_split/data/clip_repository.dart';
import 'package:vidtrans_mobile/features/clip_split/domain/clip_models.dart';
import 'package:vidtrans_mobile/features/clip_split/presentation/screens/clip_split_screen.dart';

ClipSource source({bool canCrop = true, bool ai = true}) => ClipSource.fromJson({
      'job_id': 'src1',
      'filename': 'Phim hay.mp4',
      'duration': 120.0,
      'video_url': '/api/v1/jobs/src1/artifacts/video',
      'cues': [
        {'start': 1.0, 'end': 9.0, 'text': 'Câu 1'},
        {'start': 11.0, 'end': 19.0, 'text': 'Câu 2'},
      ],
      'can_crop': canCrop,
      'ai_suggest': {'available': ai, 'reason': ai ? null : 'AI gợi ý chỉ dành cho gói trả phí. Hãy nâng cấp gói.'},
      'limits': {'min_clip_seconds': 5, 'max_clip_seconds': 600, 'max_clips': 30},
    });

class _Session extends SessionController {
  @override
  Future<Account?> build() async =>
      const Account(userId: 'u', email: 'u@t', role: 'editor', plan: 'pro', features: {'clip_split'});
}

class _FakeRepo implements ClipRepository {
  _FakeRepo(this._source);

  final ClipSource _source;
  final created = <List<ClipDraft>>[];
  String? lastWish;

  @override
  Future<ClipSourcePage> sources({String? search, int offset = 0, int limit = 20}) async => ClipSourcePage.fromJson({
        'items': [
          {'job_id': 'src1', 'filename': 'Phim hay.mp4', 'duration': 120.0, 'has_subtitles': true},
        ],
        'total': 1,
      });

  @override
  Future<ClipSource> source(String jobId) async => _source;

  @override
  Future<List<ClipDraft>> planEqual(String jobId, double clipSeconds, ClipFrame frame) async => [
        ClipDraft(start: 0, end: 60, title: 'Phần 1', frame: frame),
        ClipDraft(start: 60, end: 120, title: 'Phần 2', frame: frame),
      ];

  @override
  Future<List<ClipDraft>> suggest(String jobId,
      {String wish = '', int count = 5, double minSeconds = 15, double maxSeconds = 60,
      ClipFrame frame = ClipFrame.original}) async {
    lastWish = wish;
    return [const ClipDraft(start: 10, end: 40, title: 'Màn đối đầu')];
  }

  @override
  Future<String> create(String jobId, List<ClipDraft> clips, {bool snap = true}) async {
    created.add(clips);
    return 'batch_x';
  }

  // The video never loads in tests; the editor keeps its placeholder.
  @override
  Future<Map<String, String>> streamHeaders(String path) async => throw StateError('no video in tests');

  @override
  Uri resolve(String path) => Uri.parse('http://test$path');
}

void main() {
  group('times', () {
    test('format and parse minutes:seconds', () {
      expect(formatClipTime(83.4), '1:23.4');
      expect(formatClipTime(3725), '1:02:05.0');
      expect(formatClipTime(-3), '0:00.0');
      expect(parseClipTime('1:23.4'), 83.4);
      expect(parseClipTime('1:02:05'), 3725);
      expect(parseClipTime('83,5'), 83.5);
      expect(parseClipTime('abc'), isNull);
      expect(parseClipTime('1:2:3:4'), isNull);
    });
  });

  group('plan', () {
    test('every clip problem the server would refuse is caught first', () {
      final video = source(canCrop: false);
      expect(const ClipDraft(start: 0, end: 30).problem(video), isNull);
      expect(const ClipDraft(start: 30, end: 10).problem(video), 'Kết thúc phải sau bắt đầu');
      expect(const ClipDraft(start: 0, end: 3).problem(video), 'Ngắn hơn 5 giây');
      expect(const ClipDraft(start: 100, end: 130).problem(video), 'Nằm ngoài độ dài video');
      expect(const ClipDraft(start: 0, end: 30, frame: ClipFrame.verticalCrop).problem(video),
          'Không crop giữa được video này');
      expect(planReady(const [ClipDraft(start: 0, end: 30)], video), isTrue);
      expect(planReady(const [], video), isFalse);
    });

    test('source and draft JSON round trip', () {
      final video = source(ai: false);
      expect(video.cues.length, 2);
      expect(video.aiAvailable, isFalse);
      expect(video.aiReason, contains('nâng cấp'));
      final draft = ClipDraft.fromJson({'start': 1, 'end': 20, 'title': ' Mở đầu ', 'frame': 'vertical_blur'});
      expect(draft.frame, ClipFrame.verticalBlur);
      expect(draft.toJson(), {'start': 1.0, 'end': 20.0, 'title': 'Mở đầu', 'frame': 'vertical_blur'});
      expect(ClipFrame.parse('square'), ClipFrame.original);
    });

    test('the plan feature decides whether clips can be cut', () {
      Account withFeatures(Set<String>? features) =>
          Account(userId: 'u', email: 'u@x', role: 'editor', plan: 'free', features: features);
      expect(withFeatures({'clip_split'}).canCutClips, isTrue);
      expect(withFeatures({'photo_post'}).canCutClips, isFalse);
      expect(withFeatures(null).canCutClips, isFalse); // the server predates /api/v1/clips
    });
  });

  Future<_FakeRepo> openScreen(WidgetTester tester, ClipSource video, {String? sourceJobId, double width = 360}) async {
    tester.view.physicalSize = Size(width * 3, 2600 * 3);
    tester.view.devicePixelRatio = 3;
    addTearDown(tester.view.reset);
    final repo = _FakeRepo(video);
    await tester.pumpWidget(ProviderScope(
      overrides: [
        sessionProvider.overrideWith(_Session.new),
        clipRepositoryProvider.overrideWithValue(repo),
      ],
      child: MaterialApp(theme: AppTheme.light(), home: ClipSplitScreen(sourceJobId: sourceJobId)),
    ));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));
    return repo;
  }

  for (final width in [360.0, 412.0]) {
    testWidgets('picking a video, splitting it and creating the clips (${width.toInt()}px)', (tester) async {
      final repo = await openScreen(tester, source(), width: width);
      expect(find.text('Phim hay.mp4'), findsOneWidget);
      await tester.tap(find.text('Phim hay.mp4'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(find.text('Dài 2:00.0 · 2 câu phụ đề'), findsOneWidget);
      expect(tester.takeException(), isNull);

      await tester.tap(find.text('Chia đều'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(find.text('Tạo 2 clip'), findsOneWidget);
      expect(tester.takeException(), isNull);
      expect(repo.created, isEmpty);
    });
  }

  testWidgets('AI suggestions fill the plan with the wish sent along', (tester) async {
    final repo = await openScreen(tester, source(), sourceJobId: 'src1');
    await tester.enterText(find.widgetWithText(TextField, 'Bạn muốn cắt đoạn nào? Ví dụ: các đoạn đánh nhau, đoạn hài hước'),
        'đoạn đánh nhau');
    await tester.ensureVisible(find.text('Gợi ý'));
    await tester.tap(find.text('Gợi ý'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));
    expect(repo.lastWish, 'đoạn đánh nhau');
    expect(find.text('Tạo 1 clip'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('a free plan sees why AI is locked and crop is unavailable', (tester) async {
    final repo = await openScreen(tester, source(canCrop: false, ai: false), sourceJobId: 'src1');
    expect(find.textContaining('chỉ dành cho gói trả phí'), findsOneWidget);
    expect(find.text('Xem gói'), findsOneWidget);
    expect(find.textContaining('không dùng được kiểu crop giữa'), findsOneWidget);
    await tester.ensureVisible(find.text('Gợi ý'));
    await tester.tap(find.text('Gợi ý'));
    await tester.pump();
    expect(repo.lastWish, isNull); // the locked button sends nothing
    expect(tester.takeException(), isNull);
  });
}
