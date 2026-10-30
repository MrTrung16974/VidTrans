import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_client.dart';
import '../../../core/api/providers.dart';
import '../../../core/upload/background_uploader.dart';
import '../domain/branding_models.dart';

final brandingRepositoryProvider = Provider<BrandingRepository>(
  (ref) => BrandingRepository(ref.watch(apiClientProvider), ref.watch(backgroundUploaderProvider)),
);

class BrandingRepository {
  BrandingRepository(this._api, this._uploader);

  final ApiClient _api;
  final BackgroundUploader _uploader;

  Future<BrandingSettings> settings() async =>
      BrandingSettings.fromJson(await _api.get<Map<String, dynamic>>('/api/v1/me/branding'));

  Future<BrandingSettings> saveConfig(BrandingConfig config) async =>
      BrandingSettings.fromJson(await _api.put<Map<String, dynamic>>('/api/v1/me/branding', data: config.toJson()));

  /// [kind] is `logo` or `template`; both accept a PNG/JPEG/WebP up to 5 MB.
  Future<BrandingSettings> uploadImage(String kind, String filePath) async {
    final form = FormData.fromMap({'file': await MultipartFile.fromFile(filePath)});
    return BrandingSettings.fromJson(await _api.postMultipart<Map<String, dynamic>>('/api/v1/me/branding/$kind', form));
  }

  Future<BrandingSettings> deleteImage(String kind) async =>
      BrandingSettings.fromJson(await _api.delete<Map<String, dynamic>>('/api/v1/me/branding/$kind'));

  Future<BrandingHistory> history({int limit = 20, int offset = 0}) async => BrandingHistory.fromJson(
        await _api.get<Map<String, dynamic>>('/api/v1/branding/videos', query: {'limit': limit, 'offset': offset}),
      );

  Future<BrandingJob> job(String jobId) async =>
      BrandingJob(await _api.get<Map<String, dynamic>>('/api/v1/branding/videos/$jobId'));

  /// Returns the new job id. The same [idempotencyKey] on a retry returns the
  /// job created by the first attempt instead of a duplicate.
  Future<String> queueVideo({
    required String filePath,
    required String fileName,
    required String override,
    required bool rightsConfirmed,
    required String idempotencyKey,
    void Function(int sent, int total)? onProgress,
    CancelToken? cancelToken,
  }) async {
    // The OS uploader keeps a large video uploading in the background.
    final data = await _uploader.upload(
      '/api/v1/branding/videos',
      fields: brandingVideoFields(override: override, rightsConfirmed: rightsConfirmed),
      files: [(field: 'file', path: filePath)],
      idempotencyKey: idempotencyKey,
      cancelToken: cancelToken,
      onProgress: (progress) => onProgress?.call((progress * 1000).round(), 1000),
    );
    return data['job_id'].toString();
  }

  Future<void> reprocess(String jobId, {String override = 'auto'}) =>
      _api.post<dynamic>('/api/v1/branding/videos/$jobId/reprocess', data: {'override': override});

  Future<void> cancel(String jobId) => _api.post<dynamic>('/api/v1/jobs/$jobId/cancel');

  String downloadPath(String jobId) => '/api/v1/branding/videos/$jobId/download';
}
