import 'dart:io';

import 'package:dio/dio.dart' show CancelToken;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:vidtrans_mobile/core/api/api_client.dart';
import 'package:vidtrans_mobile/core/errors/api_exception.dart';
import 'package:vidtrans_mobile/core/storage/token_storage.dart';
import 'package:vidtrans_mobile/core/upload/background_uploader.dart';
import 'package:vidtrans_mobile/features/source_download/data/phone_video_downloader.dart';
import 'package:vidtrans_mobile/features/source_download/domain/download_policy.dart';
import 'package:vidtrans_mobile/features/video_translation/data/translation_repository.dart';
import 'package:vidtrans_mobile/features/video_translation/domain/translation_config.dart';
import 'package:vidtrans_mobile/features/video_translation/presentation/providers/submission_queue.dart';
import 'package:vidtrans_mobile/features/video_translation/presentation/providers/translate_form_controller.dart';

class _MockUploader extends Mock implements BackgroundUploader {}

ApiClient _api() => ApiClient(baseUrl: 'http://test', tokens: TokenStorage());

class _FakePolicy extends DownloadPolicyRepository {
  _FakePolicy() : super(_api());

  @override
  Future<DownloadPolicy> load() async => DownloadPolicy.fromJson({
        'platforms': {
          'TikTok': {'mode': 'client_then_server'},
          'Douyin': {'mode': 'client_then_server'},
          'YouTube': {'mode': 'server'},
        },
      });
}

class _FakeDownloader implements PhoneVideoDownloader {
  _FakeDownloader(this.dir, {this.failing = const {}});

  final Directory dir;
  final Set<String> failing;
  final calls = <String>[];

  @override
  Future<DownloadedVideo> download(String url, {void Function(double)? onProgress, CancelToken? cancelToken}) async {
    calls.add(url);
    if (failing.contains(url)) throw const PhoneDownloadException('blocked');
    final file = File('${dir.path}/${calls.length}.mp4')..writeAsBytesSync([1, 2, 3]);
    return DownloadedVideo(path: file.path, name: 'tiktok-${calls.length}.mp4', size: 3, sourceUrl: url);
  }
}

class _FakeRepository extends TranslationRepository {
  _FakeRepository() : super(_api(), _MockUploader());

  final batches = <({List<PickedMedia> files, String links})>[];
  var failNext = false;

  @override
  Future<TranslationConfig> loadConfig() async => const TranslationConfig();

  @override
  Future<void> saveConfig(TranslationConfig config) async {}

  @override
  Future<CreatedBatch> createBatch({
    required List<PickedMedia> files,
    required String sourceLinks,
    required TranslationConfig config,
    PickedMedia? backgroundMusic,
    required String idempotencyKey,
    void Function(int sent, int total)? onProgress,
    CancelToken? cancelToken,
  }) async {
    batches.add((files: files, links: sourceLinks));
    if (failNext) {
      failNext = false;
      throw const ApiException('mất mạng');
    }
    return const CreatedBatch(batchId: 'b1', name: 'Batch', jobIds: ['j1']);
  }
}

const _tiktok = 'https://www.tiktok.com/@a/video/1';
const _douyin = 'https://v.douyin.com/abc/';
const _youtube = 'https://youtu.be/xyz';

void main() {
  late Directory dir;
  late _FakeRepository repo;

  setUp(() {
    dir = Directory.systemTemp.createTempSync('phone-download');
    repo = _FakeRepository();
  });
  tearDown(() => dir.deleteSync(recursive: true));

  ProviderContainer containerWith(_FakeDownloader downloader) {
    final container = ProviderContainer(overrides: [
      translationRepositoryProvider.overrideWithValue(repo),
      downloadPolicyRepositoryProvider.overrideWithValue(_FakePolicy()),
      phoneVideoDownloaderProvider.overrideWithValue(downloader),
    ]);
    addTearDown(container.dispose);
    return container;
  }

  test('policy parses modes and maps links to the server platform names', () {
    final policy = DownloadPolicy.fromJson({
      'platforms': {
        'TikTok': {'mode': 'client_then_server'},
        'YouTube': {'mode': 'server'},
      },
    });
    expect(policy.onPhone('https://vt.tiktok.com/ZS123/'), isTrue);
    expect(policy.onPhone(_youtube), isFalse);
    expect(policy.onPhone('https://www.facebook.com/watch?v=1'), isFalse);
    expect(socialPlatformOf('https://www.iesdouyin.com/share/video/1'), 'Douyin');
    expect(socialPlatformOf('http://xhslink.com/o/jxVcmTNr5R'), 'Xiaohongshu');
    expect(socialPlatformOf('not a url'), isNull);
    expect(DownloadPolicy.serverOnly.onPhone(_tiktok), isFalse);
  });

  test('a platform the plan does not allow is never downloaded on the phone', () {
    final policy = DownloadPolicy.fromJson({
      'platforms': {
        'TikTok': {'mode': 'client_then_server', 'allowed': false},
        'Douyin': {'mode': 'client_then_server', 'allowed': true},
      },
    });
    expect(policy.allows(_tiktok), isFalse);
    expect(policy.onPhone(_tiktok), isFalse);
    expect(policy.onPhone('https://www.douyin.com/video/1'), isTrue);
  });

  test('TikTok and Douyin are uploaded from the phone, YouTube stays a server link', () async {
    final downloader = _FakeDownloader(dir);
    final container = containerWith(downloader);
    final form = container.read(translateFormProvider.notifier)..setLinks('$_tiktok\n$_youtube\n$_douyin');

    expect(await form.submit()!, isNotNull);

    final batch = repo.batches.single;
    expect(batch.links, _youtube);
    expect(batch.files.map((f) => f.sourceUrl), [_tiktok, _douyin]);
    // Uploaded downloads are removed from the phone.
    expect(batch.files.every((f) => !File(f.path).existsSync()), isTrue);
  });

  test('a link the phone cannot get falls back to the server', () async {
    final container = containerWith(_FakeDownloader(dir, failing: {_tiktok}));
    final form = container.read(translateFormProvider.notifier)..setLinks('$_tiktok $_douyin');

    await form.submit()!;

    expect(repo.batches.single.links, _tiktok);
    expect(repo.batches.single.files.single.sourceUrl, _douyin);
  });

  test('retry after an upload error reuses the phone download', () async {
    final downloader = _FakeDownloader(dir);
    final container = containerWith(downloader);
    final form = container.read(translateFormProvider.notifier)..setLinks(_tiktok);
    repo.failNext = true;

    expect(await form.submit()!, isNull);
    final failed = container.read(submissionQueueProvider).single;
    expect(failed.error, 'mất mạng');
    expect(await container.read(submissionQueueProvider.notifier).retry(failed.id), isNotNull);
    expect(container.read(submissionQueueProvider), isEmpty);

    expect(downloader.calls, [_tiktok]);
    expect(repo.batches.map((b) => b.files.single.path).toSet(), hasLength(1));
  });

  test('submit frees the form at once while the phone download runs in the background', () async {
    final container = containerWith(_FakeDownloader(dir));
    final form = container.read(translateFormProvider.notifier)..setLinks(_tiktok);

    final done = form.submit()!;

    expect(container.read(translateFormProvider).sourceCount, 0);
    expect(container.read(submissionQueueProvider).single.sourceCount, 1);
    expect(await done, isNotNull);
    expect(container.read(submissionQueueProvider), isEmpty);
  });
}
