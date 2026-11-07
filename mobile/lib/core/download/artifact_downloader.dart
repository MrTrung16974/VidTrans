import 'dart:async';
import 'dart:io';

import 'package:background_downloader/background_downloader.dart';
import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import '../api/api_client.dart';
import '../api/providers.dart';
import '../errors/api_exception.dart';

final artifactDownloaderProvider = Provider<ArtifactDownloader>(
  (ref) => ArtifactDownloader(ref.watch(apiClientProvider)),
);

enum ArtifactTransferStatus { downloading, completed, failed }

class ArtifactDownloadTaskState {
  const ArtifactDownloadTaskState({
    required this.url,
    required this.fileName,
    required this.label,
    this.jobTitle,
    this.status = ArtifactTransferStatus.downloading,
    this.progress,
    this.filePath,
    this.sharedStoragePath,
    this.error,
  });

  final String url;
  final String fileName;
  final String label;
  final String? jobTitle;
  final ArtifactTransferStatus status;

  /// `0.0..1.0` when known, or `null` while indeterminate.
  final double? progress;
  final String? filePath;
  final String? sharedStoragePath;
  final String? error;

  bool get isDownloading => status == ArtifactTransferStatus.downloading;
  bool get isCompleted => status == ArtifactTransferStatus.completed;
  bool get isFailed => status == ArtifactTransferStatus.failed;

  ArtifactDownloadTaskState copyWith({
    ArtifactTransferStatus? status,
    double? progress,
    String? filePath,
    String? sharedStoragePath,
    String? error,
    bool clearError = false,
  }) => ArtifactDownloadTaskState(
    url: url,
    fileName: fileName,
    label: label,
    jobTitle: jobTitle,
    status: status ?? this.status,
    progress: progress ?? this.progress,
    filePath: filePath ?? this.filePath,
    sharedStoragePath: sharedStoragePath ?? this.sharedStoragePath,
    error: clearError ? null : (error ?? this.error),
  );
}

final artifactDownloadControllerProvider =
    NotifierProvider<
      ArtifactDownloadController,
      Map<String, ArtifactDownloadTaskState>
    >(ArtifactDownloadController.new);

/// Global download manager that keeps artifact downloads alive across screen
/// navigation and app backgrounding.
class ArtifactDownloadController
    extends Notifier<Map<String, ArtifactDownloadTaskState>> {
  final Map<String, CancelToken> _cancelTokens = {};
  final Map<String, Future<File?>> _inFlight = {};

  ArtifactDownloader get _downloader => ref.read(artifactDownloaderProvider);

  @override
  Map<String, ArtifactDownloadTaskState> build() => const {};

  /// Checks if an artifact is already cached locally on disk and populates state.
  Future<void> probeCached({
    required String url,
    required String fileName,
    required String label,
    String? jobTitle,
  }) async {
    final current = state[url];
    if (current != null &&
        (current.isDownloading ||
            (current.isCompleted && current.filePath != null))) {
      return;
    }
    final cached = await _downloader.existingFile(fileName);
    if (cached != null) {
      state = {
        ...state,
        url: ArtifactDownloadTaskState(
          url: url,
          fileName: fileName,
          label: label,
          jobTitle: jobTitle,
          status: ArtifactTransferStatus.completed,
          progress: 1.0,
          filePath: cached.path,
        ),
      };
    }
  }

  /// Starts or attaches to an OS-backed background download.
  /// Continues running even if the user navigates away or minimizes the app.
  Future<File?> startDownload({
    required String url,
    required String fileName,
    required String label,
    String? jobTitle,
    bool forceReload = false,
  }) {
    final existingFuture = _inFlight[url];
    if (existingFuture != null) return existingFuture;

    final future = _runDownload(
      url: url,
      fileName: fileName,
      label: label,
      jobTitle: jobTitle,
      forceReload: forceReload,
    );
    _inFlight[url] = future;
    future.whenComplete(() => _inFlight.remove(url));
    return future;
  }

  Future<File?> _runDownload({
    required String url,
    required String fileName,
    required String label,
    String? jobTitle,
    required bool forceReload,
  }) async {
    if (!forceReload) {
      final cached = await _downloader.existingFile(fileName);
      if (cached != null) {
        state = {
          ...state,
          url: ArtifactDownloadTaskState(
            url: url,
            fileName: fileName,
            label: label,
            jobTitle: jobTitle,
            status: ArtifactTransferStatus.completed,
            progress: 1.0,
            filePath: cached.path,
          ),
        };
        return cached;
      }
    }

    final cancelToken = CancelToken();
    _cancelTokens[url] = cancelToken;
    state = {
      ...state,
      url: ArtifactDownloadTaskState(
        url: url,
        fileName: fileName,
        label: label,
        jobTitle: jobTitle,
        status: ArtifactTransferStatus.downloading,
        progress: null,
      ),
    };

    try {
      final result = await _downloader.downloadWithPublicCopy(
        url,
        fileName: fileName,
        cancelToken: cancelToken,
        forceReload: forceReload,
        onProgress: (received, total) {
          if (total <= 0) return;
          final current = state[url];
          if (current == null || !current.isDownloading) return;
          state = {
            ...state,
            url: current.copyWith(
              progress: (received / total).clamp(0.0, 1.0),
            ),
          };
        },
      );
      state = {
        ...state,
        url: ArtifactDownloadTaskState(
          url: url,
          fileName: fileName,
          label: label,
          jobTitle: jobTitle,
          status: ArtifactTransferStatus.completed,
          progress: 1.0,
          filePath: result.file.path,
          sharedStoragePath: result.sharedStoragePath,
        ),
      };
      return result.file;
    } catch (error) {
      if (cancelToken.isCancelled) {
        final next = Map<String, ArtifactDownloadTaskState>.from(state)
          ..remove(url);
        state = next;
        return null;
      }
      state = {
        ...state,
        url: ArtifactDownloadTaskState(
          url: url,
          fileName: fileName,
          label: label,
          jobTitle: jobTitle,
          status: ArtifactTransferStatus.failed,
          error: errorMessage(error),
        ),
      };
      rethrow;
    } finally {
      _cancelTokens.remove(url);
    }
  }

  /// Explicitly cancels an active download when the user taps Cancel.
  void cancel(String url) {
    final token = _cancelTokens.remove(url);
    token?.cancel('Người dùng hủy tải');
    final next = Map<String, ArtifactDownloadTaskState>.from(state)..remove(url);
    state = next;
  }

  Future<void> shareFile(String filePath, {String? text}) =>
      _downloader.share(File(filePath), text: text);
}

typedef DownloadedArtifactResult = ({File file, String? sharedStoragePath});

/// Downloads authenticated results (MP4, SRT, ZIP...) using the native OS
/// background transfer service (`URLSession` on iOS, `WorkManager` /
/// Foreground Service on Android) so downloads continue when the user switches
/// screens or minimizes the app, and saves a copy to the device's Downloads /
/// Videos storage automatically.
class ArtifactDownloader {
  ArtifactDownloader(this._api, [FileDownloader? downloader])
    : _downloader = downloader ?? FileDownloader();

  final ApiClient _api;
  final FileDownloader _downloader;
  static bool _notificationsConfigured = false;

  void _ensureNotificationsConfigured() {
    if (_notificationsConfigured) return;
    _notificationsConfigured = true;
    try {
      _downloader.configureNotification(
        running: const TaskNotification(
          'Đang tải {displayName}',
          'Tiến trình chạy nền · {progress}',
        ),
        complete: const TaskNotification(
          'Đã tải xong {displayName}',
          'Tệp đã được lưu vào thiết bị',
        ),
        error: const TaskNotification(
          'Tải tệp thất bại',
          'Không thể tải {displayName}',
        ),
        paused: const TaskNotification(
          'Đã tạm dừng {displayName}',
          'Mở lại ứng dụng để tiếp tục',
        ),
        progressBar: true,
      );
    } catch (_) {
      // Ignored in headless test environments without platform channels.
    }
  }

  Future<File?> existingFile(String fileName) async {
    try {
      final dir = Directory(
        '${(await getApplicationDocumentsDirectory()).path}/downloads',
      );
      final file = File('${dir.path}/${_safeName(fileName)}');
      if (await file.exists() && await file.length() > 0) {
        return file;
      }
    } catch (_) {}
    return null;
  }

  Future<File> download(
    String path, {
    required String fileName,
    void Function(int received, int total)? onProgress,
    CancelToken? cancelToken,
    bool forceReload = false,
  }) async {
    final res = await downloadWithPublicCopy(
      path,
      fileName: fileName,
      onProgress: onProgress,
      cancelToken: cancelToken,
      forceReload: forceReload,
    );
    return res.file;
  }

  Future<DownloadedArtifactResult> downloadWithPublicCopy(
    String path, {
    required String fileName,
    void Function(int received, int total)? onProgress,
    CancelToken? cancelToken,
    bool forceReload = false,
  }) async {
    final safeName = _safeName(fileName);
    final dir = Directory(
      '${(await getApplicationDocumentsDirectory()).path}/downloads',
    );
    await dir.create(recursive: true);
    final file = File('${dir.path}/$safeName');

    if (!forceReload && await file.exists() && await file.length() > 0) {
      final len = await file.length();
      onProgress?.call(len, len);
      return (file: file, sharedStoragePath: null);
    }

    _ensureNotificationsConfigured();

    // Try native OS background downloader first so minimizing the app never
    // interrupts the download.
    final osResult = await _tryBackgroundDownload(
      path,
      safeName: safeName,
      displayName: fileName,
      targetFile: file,
      onProgress: onProgress,
      cancelToken: cancelToken,
    );
    if (osResult != null) {
      return osResult;
    }

    // Fallback to Dio when running in unit tests or if the native channel is unavailable.
    try {
      await _api.dio.download(
        path,
        file.path,
        onReceiveProgress: onProgress,
        cancelToken: cancelToken,
      );
    } on DioException catch (error) {
      if (await file.exists()) await file.delete();
      throw ApiException.fromDio(error);
    }
    return (file: file, sharedStoragePath: null);
  }

  Future<DownloadedArtifactResult?> _tryBackgroundDownload(
    String path, {
    required String safeName,
    required String displayName,
    required File targetFile,
    void Function(int received, int total)? onProgress,
    CancelToken? cancelToken,
  }) async {
    try {
      var result = await _runTask(
        path,
        safeName: safeName,
        displayName: displayName,
        onProgress: onProgress,
        cancelToken: cancelToken,
      );
      if (result.update.responseStatusCode == 401 &&
          !(cancelToken?.isCancelled ?? false) &&
          await _api.refreshAccessToken() != null) {
        result = await _runTask(
          path,
          safeName: safeName,
          displayName: displayName,
          onProgress: onProgress,
          cancelToken: cancelToken,
        );
      }

      final update = result.update;
      if (update.status == TaskStatus.canceled ||
          (cancelToken?.isCancelled ?? false)) {
        if (await targetFile.exists()) await targetFile.delete();
        throw const ApiException('Đã hủy tải tệp');
      }

      if (update.status == TaskStatus.complete &&
          await targetFile.exists() &&
          await targetFile.length() > 0) {
        final sharedPath = await _copyToSharedStorage(result.task, targetFile);
        return (file: targetFile, sharedStoragePath: sharedPath);
      }

      final status = update.responseStatusCode;
      if (status != null && status >= 400) {
        if (await targetFile.exists()) await targetFile.delete();
        throw ApiException('Không tải được tệp (HTTP $status)', statusCode: status);
      }
      return null;
    } on ApiException {
      rethrow;
    } catch (_) {
      // Platform channel unavailable or failed before transfer; let Dio handle it.
      return null;
    }
  }

  Future<({DownloadTask task, TaskStatusUpdate update})> _runTask(
    String path, {
    required String safeName,
    required String displayName,
    void Function(int received, int total)? onProgress,
    CancelToken? cancelToken,
  }) async {
    final task = DownloadTask(
      url: _api.resolve(path).toString(),
      headers: await _api.authHeaders(),
      filename: safeName,
      directory: 'downloads',
      baseDirectory: BaseDirectory.applicationDocuments,
      updates: Updates.statusAndProgress,
      retries: 3,
      allowPause: true,
      displayName: displayName,
    );
    cancelToken?.whenCancel.then(
      (_) => _downloader.cancelTaskWithId(task.taskId),
    );
    final update = await _downloader.download(
      task,
      onProgress: (progress) {
        if (progress >= 0) {
          onProgress?.call((progress * 1000).round(), 1000);
        }
      },
    );
    return (task: task, update: update);
  }

  /// Copies the finished file into the phone's public Downloads / Video folder
  /// while keeping the local copy in `applicationDocuments/downloads` for
  /// instant in-app sharing without loading the file into memory.
  Future<String?> _copyToSharedStorage(
    DownloadTask task,
    File localFile,
  ) async {
    File? backupFile;
    try {
      backupFile = await localFile.copy('${localFile.path}.keep');
      final lower = localFile.path.toLowerCase();
      final destination =
          (lower.endsWith('.mp4') ||
              lower.endsWith('.mov') ||
              lower.endsWith('.mkv'))
          ? SharedStorage.video
          : SharedStorage.downloads;
      final sharedPath = await _downloader.moveToSharedStorage(
        task,
        destination,
      );
      if (!await localFile.exists() && await backupFile.exists()) {
        await backupFile.rename(localFile.path);
      } else if (await backupFile.exists()) {
        await backupFile.delete();
      }
      return sharedPath;
    } catch (_) {
      try {
        if (!await localFile.exists() &&
            backupFile != null &&
            await backupFile.exists()) {
          await backupFile.rename(localFile.path);
        } else if (backupFile != null && await backupFile.exists()) {
          await backupFile.delete();
        }
      } catch (_) {}
      return null;
    }
  }

  Future<void> share(File file, {String? text}) =>
      SharePlus.instance.share(ShareParams(files: [XFile(file.path)], text: text));

  static String _safeName(String name) =>
      name.replaceAll(RegExp(r'[\\/:*?"<>|]'), '_');
}
