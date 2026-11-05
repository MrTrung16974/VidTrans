import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_client.dart';
import '../../../core/api/providers.dart';
import '../domain/job.dart';
import '../domain/job_list.dart';

final jobsRepositoryProvider = Provider<JobsRepository>((ref) => JobsRepository(ref.watch(apiClientProvider)));

class JobsRepository {
  JobsRepository(this._api);

  final ApiClient _api;

  Future<JobList> list({int page = 1, int limit = 20, String? status, String? search}) async {
    final data = await _api.get<Map<String, dynamic>>('/api/v1/jobs', query: {
      'page': page,
      'limit': limit,
      if (status != null && status.isNotEmpty) 'status': status,
      if (search != null && search.isNotEmpty) 'search': search,
    });
    return JobList.fromJson(data);
  }

  Future<Job> get(String jobId) async =>
      Job.fromJson(await _api.get<Map<String, dynamic>>('/api/v1/jobs/$jobId'));

  Future<void> cancel(String jobId) => _api.post<dynamic>('/api/v1/jobs/$jobId/cancel');

  Future<void> retry(String jobId) => _api.post<dynamic>('/api/v1/jobs/$jobId/retry');

  /// Re-runs a voiceless job as "Chỉ tải video": the source, no subtitles or dubbing.
  Future<void> downloadOnly(String jobId) => _api.post<dynamic>('/api/v1/jobs/$jobId/download-only');

  /// Answers a photo post waiting in `awaiting_input`: its images as a zip
  /// (`action: images`) or a slideshow of [minutes] (`action: video`).
  Future<void> choosePhotoPost(String jobId, {required String action, double? minutes, String? musicTrackId}) =>
      _api.post<dynamic>('/api/v1/jobs/$jobId/photo-post', data: {
        'action': action,
        'minutes': ?minutes,
        'music_track_id': ?(musicTrackId == null || musicTrackId.isEmpty ? null : musicTrackId),
      });

  Future<void> delete(String jobId) => _api.delete<dynamic>('/api/v1/jobs/$jobId');

  Uri artifactUri(String path) => _api.resolve(path);

  Future<Map<String, String>> authHeaders() => _api.authHeaders();

  /// Checks the artifact is reachable (refreshing the token if needed) and
  /// returns headers carrying a valid access token for streaming it.
  Future<Map<String, String>> streamHeaders(String path) async {
    await _api.probe(path);
    return _api.authHeaders();
  }
}
