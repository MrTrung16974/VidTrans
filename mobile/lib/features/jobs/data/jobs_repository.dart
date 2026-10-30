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

  Future<void> delete(String jobId) => _api.delete<dynamic>('/api/v1/jobs/$jobId');

  Uri artifactUri(String path) => _api.resolve(path);

  Future<Map<String, String>> authHeaders() => _api.authHeaders();
}
