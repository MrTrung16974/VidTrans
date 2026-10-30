import 'dart:convert';

import 'package:background_downloader/background_downloader.dart';
import 'package:dio/dio.dart' show CancelToken;
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
  BackgroundUploader(this._api, [FileDownloader? downloader]) : _downloader = downloader ?? FileDownloader();

  final ApiClient _api;
  final FileDownloader _downloader;

  Future<Map<String, dynamic>> upload(
    String path, {
    required Map<String, String> fields,
    required List<UploadFile> files,
    String? idempotencyKey,
    void Function(double progress)? onProgress,
    CancelToken? cancelToken,
  }) async {
    var result = await _send(path, fields, files, idempotencyKey, onProgress, cancelToken);
    if (result.responseStatusCode == 401 &&
        !(cancelToken?.isCancelled ?? false) &&
        await _api.refreshAccessToken() != null) {
      // Same Idempotency-Key: a request the server did accept is not queued twice.
      result = await _send(path, fields, files, idempotencyKey, onProgress, cancelToken);
    }
    return _decode(result);
  }

  Future<TaskStatusUpdate> _send(
    String path,
    Map<String, String> fields,
    List<UploadFile> files,
    String? idempotencyKey,
    void Function(double progress)? onProgress,
    CancelToken? cancelToken,
  ) async {
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
      retries: 2,
      displayName: 'VidTrans',
    );
    cancelToken?.whenCancel.then((_) => _downloader.cancelTaskWithId(task.taskId));
    return _downloader.upload(task, onProgress: (progress) {
      if (progress >= 0) onProgress?.call(progress);
    });
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
