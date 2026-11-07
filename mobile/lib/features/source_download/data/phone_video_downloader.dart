import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:background_downloader/background_downloader.dart';
import 'package:dio/dio.dart' show CancelToken;
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_client.dart';
import '../../../core/api/providers.dart';
import '../domain/download_policy.dart';
import 'extract_script.dart';

final phoneVideoDownloaderProvider = Provider<PhoneVideoDownloader>((ref) => WebViewVideoDownloader());

final downloadPolicyRepositoryProvider =
    Provider<DownloadPolicyRepository>((ref) => DownloadPolicyRepository(ref.watch(apiClientProvider)));

class DownloadPolicyRepository {
  DownloadPolicyRepository(this._api);

  final ApiClient _api;

  /// Never throws: without a policy every link goes to the server, as before.
  Future<DownloadPolicy> load() async {
    try {
      return DownloadPolicy.fromJson(await _api.get<Map<String, dynamic>>('/api/v1/download/policy'));
    } catch (_) {
      return DownloadPolicy.serverOnly;
    }
  }
}

/// A video saved in the app's temporary directory, ready for upload.
class DownloadedVideo {
  const DownloadedVideo({required this.path, required this.name, required this.size, required this.sourceUrl});

  final String path;
  final String name;
  final int size;

  /// The link the user entered, sent to the server as `file_source_urls`.
  final String sourceUrl;
}

class PhoneDownloadException implements Exception {
  const PhoneDownloadException(this.message);

  final String message;

  @override
  String toString() => message;
}

abstract class PhoneVideoDownloader {
  /// Downloads a TikTok/Douyin link on the phone. Throws [PhoneDownloadException].
  Future<DownloadedVideo> download(
    String url, {
    void Function(double progress)? onProgress,
    CancelToken? cancelToken,
  });
}

/// Opens the link in a hidden WebView (a real browser on the user's network,
/// with the cookies of any login made in the app), reads the video address the
/// page itself plays, then downloads that file with the WebView's cookies,
/// User-Agent and Referer so the CDN sees the same client.
class WebViewVideoDownloader implements PhoneVideoDownloader {
  WebViewVideoDownloader([FileDownloader? downloader]) : _downloader = downloader ?? FileDownloader();

  static const _pageTimeout = Duration(seconds: 15);
  static const _pollInterval = Duration(milliseconds: 650);

  /// A smaller "video" is an error page or a placeholder.
  static const _minVideoBytes = 32 * 1024;

  final FileDownloader _downloader;

  @override
  Future<DownloadedVideo> download(
    String url, {
    void Function(double progress)? onProgress,
    CancelToken? cancelToken,
  }) async {
    final platform = socialPlatformOf(url) ?? 'video';
    final found = await _extract(url, cancelToken);
    final headers = {
      'User-Agent': found.userAgent,
      'Referer': found.pageUrl,
      if (found.cookies.isNotEmpty) 'Cookie': found.cookies,
    };
    final name = '${platform.toLowerCase()}-${found.id ?? DateTime.now().millisecondsSinceEpoch}.mp4';
    final task = DownloadTask(
      url: found.mediaUrl,
      headers: headers,
      filename: name,
      directory: 'phone_downloads',
      baseDirectory: BaseDirectory.temporary,
      updates: Updates.statusAndProgress,
      retries: 2,
      allowPause: true,
      displayName: name,
    );
    cancelToken?.whenCancel.then((_) => _downloader.cancelTaskWithId(task.taskId));
    final result = await _downloader.download(task, onProgress: (progress) {
      if (progress >= 0) onProgress?.call(progress);
    });
    final file = File(await task.filePath());
    if (result.status == TaskStatus.canceled) throw const PhoneDownloadException('Đã hủy tải video');
    final size = await file.exists() ? await file.length() : 0;
    final mime = result.mimeType ?? '';
    if (result.status != TaskStatus.complete || size < _minVideoBytes || mime.startsWith('text/')) {
      if (await file.exists()) await file.delete();
      throw PhoneDownloadException(
        'Không tải được file video $platform'
        '${result.responseStatusCode == null ? '' : ' (HTTP ${result.responseStatusCode})'}',
      );
    }
    return DownloadedVideo(path: file.path, name: name, size: size, sourceUrl: url);
  }

  Future<_FoundVideo> _extract(String url, CancelToken? cancelToken) async {
    final found = Completer<_FoundVideo>();
    Timer? poll;
    late final HeadlessInAppWebView webView;

    Future<void> tryExtract(InAppWebViewController controller) async {
      if (found.isCompleted) return;
      final raw = await controller.evaluateJavascript(source: extractVideoScript);
      if (raw is! String || found.isCompleted) return;
      final data = jsonDecode(raw) as Map<String, dynamic>;
      if (data['error'] is String) {
        found.completeError(PhoneDownloadException(data['error'] as String));
        return;
      }
      final mediaUrl = data['url'] as String;
      final pageUrl = (await controller.getUrl())?.toString() ?? url;
      final userAgent = await controller.evaluateJavascript(source: 'navigator.userAgent') as String? ?? '';
      final cookies = await CookieManager.instance().getCookies(url: WebUri(mediaUrl));
      if (found.isCompleted) return;
      found.complete(_FoundVideo(
        mediaUrl: mediaUrl,
        pageUrl: pageUrl,
        userAgent: userAgent,
        cookies: cookies.map((c) => '${c.name}=${c.value}').join('; '),
        id: data['id'] as String?,
      ));
    }

    webView = HeadlessInAppWebView(
      initialUrlRequest: URLRequest(url: WebUri(url)),
      initialSettings: InAppWebViewSettings(
        mediaPlaybackRequiresUserGesture: true,
        allowsInlineMediaPlayback: true,
        javaScriptEnabled: true,
      ),
      onLoadStop: (controller, _) {
        // Data often arrives after the load event (client-side rendering): keep checking.
        poll ??= Timer.periodic(_pollInterval, (_) => tryExtract(controller).catchError((_) {}));
        tryExtract(controller).catchError((_) {});
      },
    );
    cancelToken?.whenCancel.then((_) {
      if (!found.isCompleted) found.completeError(const PhoneDownloadException('Đã hủy tải video'));
    });
    try {
      await webView.run();
      return await found.future.timeout(
        _pageTimeout,
        onTimeout: () => throw const PhoneDownloadException('Trang không trả về video (có thể cần đăng nhập)'),
      );
    } finally {
      poll?.cancel();
      await webView.dispose();
    }
  }
}

class _FoundVideo {
  const _FoundVideo({
    required this.mediaUrl,
    required this.pageUrl,
    required this.userAgent,
    required this.cookies,
    this.id,
  });

  final String mediaUrl;
  final String pageUrl;
  final String userAgent;
  final String cookies;
  final String? id;
}
