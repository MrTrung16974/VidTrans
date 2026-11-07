import 'dart:async';
import 'dart:convert';

import 'package:background_downloader/background_downloader.dart';
import 'package:dio/dio.dart' show CancelToken, FormData, MultipartFile;
import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../api/providers.dart';
import '../errors/api_exception.dart';

final backgroundUploaderProvider =
    Provider<BackgroundUploader>((ref) => BackgroundUploader(ref.watch(apiClientProvider)));

/// One file part of a multipart upload: form field name and absolute path.
typedef UploadFile = ({String field, String path});

/// Multipart uploads run by the OS (URLSession background session on iOS,
/// WorkManager on Android), so a large video keeps uploading when the user
/// leaves the app — within what the OS allows; a force-quit app is not resumed.
///
/// Drop-in for `ApiClient.postMultipart` when sending files from disk:
///
/// ```dart
/// final result = await ref.read(backgroundUploaderProvider).upload(
///   '/api/v1/batches',
///   fields: {'mode': '2', 'target_language': 'vi'},
///   files: [(field: 'files', path: picked.path)],
///   idempotencyKey: key,           // same key when the user retries
///   onProgress: (p) => setState(() => progress = p),
/// );
/// ```
///
/// Repeated form fields (lists) are not supported by the OS uploaders; send
/// them as JSON if an endpoint needs them, or keep using `postMultipart`.
class BackgroundUploader {
  BackgroundUploader(
    this._api, [
    FileDownloader? downloader,
    this.startTimeout = const Duration(seconds: 25),
  ]) : _downloader = downloader ?? FileDownloader();

  final ApiClient _api;
  final FileDownloader _downloader;
  static bool _notificationsConfigured = false;

  /// How long the OS queue may hold a task before it starts while the app is
  /// in the foreground. WorkManager only runs it on a network Android has
  /// validated as having Internet, so on a company/captive Wi-Fi (or a dev
  /// server reached through `adb reverse`) the task would wait forever without
  /// an error. Past this delay (only while in foreground) the task is cancelled
  /// and the file is sent directly from the app instead.
  final Duration startTimeout;

  void _ensureNotificationsConfigured() {
    if (_notificationsConfigured) return;
    _notificationsConfigured = true;
    try {
      _downloader.configureNotificationForGroup(
        FileDownloader.defaultGroup,
        running: const TaskNotification(
          'Đang xử lý {displayName}',
          'Đang chạy nền · {progress}',
        ),
        complete: const TaskNotification(
          'Hoàn tất {displayName}',
          'Tác vụ nền đã hoàn thành',
        ),
        error: const TaskNotification(
          'Tác vụ nền thất bại',
          'Không thể hoàn tất {displayName}',
        ),
        paused: const TaskNotification(
          'Đã tạm dừng {displayName}',
          'Mở lại ứng dụng để tiếp tục',
        ),
        progressBar: true,
      );
    } catch (_) {}
  }

  static bool _isAppBackgrounded() {
    try {
      final state = WidgetsBinding.instance.lifecycleState;
      return state != null && state != AppLifecycleState.resumed;
    } catch (_) {
      return false;
    }
  }

  Future<Map<String, dynamic>> upload(
    String path, {
    required Map<String, String> fields,
    required List<UploadFile> files,
    String? idempotencyKey,
    void Function(double progress)? onProgress,
    CancelToken? cancelToken,
  }) async {
    var result = await _send(path, fields, files, idempotencyKey, onProgress, cancelToken);
    if (result == null) {
      return _uploadDirect(path, fields, files, idempotencyKey, onProgress, cancelToken);
    }
    if (result.responseStatusCode == 401 &&
        !(cancelToken?.isCancelled ?? false) &&
        await _api.refreshAccessToken() != null) {
      // Same Idempotency-Key: a request the server did accept is not queued twice.
      result = await _send(path, fields, files, idempotencyKey, onProgress, cancelToken);
      if (result == null) {
        return _uploadDirect(path, fields, files, idempotencyKey, onProgress, cancelToken);
      }
    }
    return _decode(result);
  }

  /// In-app multipart upload through Dio (refreshes the token on 401 itself).
  Future<Map<String, dynamic>> _uploadDirect(
    String path,
    Map<String, String> fields,
    List<UploadFile> files,
    String? idempotencyKey,
    void Function(double progress)? onProgress,
    CancelToken? cancelToken,
  ) async {
    final form = FormData.fromMap(fields);
    for (final file in files) {
      form.files.add(MapEntry(file.field, await MultipartFile.fromFile(file.path)));
    }
    return _api.postMultipart<Map<String, dynamic>>(
      path,
      form,
      idempotencyKey: idempotencyKey,
      cancelToken: cancelToken,
      onProgress: (sent, total) {
        if (total > 0) onProgress?.call(sent / total);
      },
    );
  }

  /// Null when the OS never started the task within [startTimeout].
  Future<TaskStatusUpdate?> _send(
    String path,
    Map<String, String> fields,
    List<UploadFile> files,
    String? idempotencyKey,
    void Function(double progress)? onProgress,
    CancelToken? cancelToken,
  ) async {
    _ensureNotificationsConfigured();
    final task = MultiUploadTask(
      url: _api.resolve(path).toString(),
      files: [for (final file in files) (file.field, file.path)],
      fields: fields,
      headers: {
        ...await _api.authHeaders(),
        'Accept': 'application/json',
        'Idempotency-Key': ?idempotencyKey,
      },
      httpRequestMethod: 'POST',
      updates: Updates.statusAndProgress,
      retries: 3,
      displayName: 'VidTrans',
    );
    cancelToken?.whenCancel.then((_) => _downloader.cancelTaskWithId(task.taskId));
    var started = false;
    var stalled = false;
    final watchdog = Timer(startTimeout, () {
      // Never cancel the OS background upload task if the user minimized the
      // app: Dio fallback cannot run in background, whereas WorkManager /
      // URLSession will finish the upload.
      if (started || _isAppBackgrounded()) return;
      stalled = true;
      _downloader.cancelTaskWithId(task.taskId);
    });
    try {
      final result = await _downloader.upload(
        task,
        onStatus: (status) {
          if (status != TaskStatus.enqueued) started = true;
        },
        onProgress: (progress) {
          if (progress >= 0) {
            started = true;
            onProgress?.call(progress);
          }
        },
      );
      if (stalled && result.status == TaskStatus.canceled && !(cancelToken?.isCancelled ?? false)) {
        return null;
      }
      return result;
    } finally {
      watchdog.cancel();
    }
  }

  static Map<String, dynamic> _decode(TaskStatusUpdate result) {
    final status = result.responseStatusCode;
    Object? body;
    try {
      body = result.responseBody == null ? null : jsonDecode(result.responseBody!);
    } on FormatException {
      body = null;
    }
    if (result.status == TaskStatus.complete && body is Map<String, dynamic>) return body;
    if (result.status == TaskStatus.canceled) throw const ApiException('Đã hủy tải lên');
    String? message;
    if (body is Map && body['detail'] is String) message = body['detail'] as String;
    throw ApiException(
      message ?? result.exception?.description ?? 'Tải lên thất bại${status == null ? '' : ' ($status)'}',
      statusCode: status,
    );
  }
}
