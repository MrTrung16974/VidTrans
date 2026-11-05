import 'package:dio/dio.dart';
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
  /// [musicTrackIds] are library tracks played one after another; empty keeps the post's music.
  Future<void> choosePhotoPost(String jobId, {required String action, double? minutes, List<String>? musicTrackIds}) =>
      _api.post<dynamic>('/api/v1/jobs/$jobId/photo-post', data: {
        'action': action,
        'minutes': ?minutes,
        'music_track_ids': ?musicTrackIds,
      });

  /// Library tracks that AI thinks fit a waiting photo post, best first.
  Future<MusicSuggestions> photoPostMusicSuggestions(String jobId) async => MusicSuggestions.fromJson(
        await _api.post<Map<String, dynamic>>(
          '/api/v1/jobs/$jobId/photo-post/music-suggestions',
          // The model may look at the post's pictures; the server gives it up to 2 minutes.
          options: Options(receiveTimeout: const Duration(seconds: 150)),
        ),
      );

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

/// A library track suggested for a photo post, with why it fits.
class MusicSuggestion {
  const MusicSuggestion({required this.trackId, required this.title, required this.category, required this.reason});

  final String trackId;
  final String title;
  final String category;
  final String reason;

  factory MusicSuggestion.fromJson(Map<String, dynamic> json) => MusicSuggestion(
        trackId: json['track_id'].toString(),
        title: json['title'] as String? ?? '',
        category: json['category'] as String? ?? '',
        reason: json['reason'] as String? ?? '',
      );
}

class MusicSuggestions {
  const MusicSuggestions({required this.items, required this.mood, required this.byAi});

  final List<MusicSuggestion> items;
  final String mood;

  /// False when no AI model answered and the caption's mood words picked the tracks.
  final bool byAi;

  factory MusicSuggestions.fromJson(Map<String, dynamic> json) => MusicSuggestions(
        items: [
          for (final item in json['items'] as List? ?? const [])
            if (item is Map) MusicSuggestion.fromJson(Map<String, dynamic>.from(item)),
        ],
        mood: json['mood'] as String? ?? '',
        byAi: (json['source'] as String? ?? 'heuristic') != 'heuristic',
      );
}
