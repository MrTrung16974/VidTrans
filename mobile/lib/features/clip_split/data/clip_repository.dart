import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_client.dart';
import '../../../core/api/providers.dart';
import '../domain/clip_models.dart';

final clipRepositoryProvider = Provider<ClipRepository>((ref) => ClipRepository(ref.watch(apiClientProvider)));

/// `/api/v1/clips`: finished videos to cut, plans and the clip jobs.
class ClipRepository {
  ClipRepository(this._api);

  final ApiClient _api;

  Future<ClipSourcePage> sources({String? search, int offset = 0, int limit = 20}) async =>
      ClipSourcePage.fromJson(await _api.get<Map<String, dynamic>>('/api/v1/clips/sources', query: {
        'limit': limit,
        'offset': offset,
        if (search != null && search.trim().isNotEmpty) 'search': search.trim(),
      }));

  Future<ClipSource> source(String jobId) async =>
      ClipSource.fromJson(await _api.get<Map<String, dynamic>>('/api/v1/clips/sources/$jobId'));

  /// Back-to-back clips of about [clipSeconds], each ending between two sentences.
  Future<List<ClipDraft>> planEqual(String jobId, double clipSeconds, ClipFrame frame) async =>
      _clips(await _api.post<Map<String, dynamic>>('/api/v1/clips/plan', data: {
        'source_job_id': jobId,
        'clip_seconds': clipSeconds,
        'frame': frame.key,
      }));

  /// AI-picked moments, steered by what the user wants ([wish]). Paid plans only.
  Future<List<ClipDraft>> suggest(
    String jobId, {
    String wish = '',
    int count = 5,
    double minSeconds = 15,
    double maxSeconds = 60,
    ClipFrame frame = ClipFrame.original,
  }) async =>
      _clips(await _api.post<Map<String, dynamic>>('/api/v1/clips/suggest', data: {
        'source_job_id': jobId,
        'wish': wish.trim(),
        'count': count,
        'min_seconds': minSeconds,
        'max_seconds': maxSeconds,
        'frame': frame.key,
      }));

  /// Queues one clip job per clip (one batch); returns the batch id.
  Future<String> create(String jobId, List<ClipDraft> clips, {bool snap = true}) async {
    final created = await _api.post<Map<String, dynamic>>('/api/v1/clips', data: {
      'source_job_id': jobId,
      'snap': snap,
      'clips': [for (final clip in clips) clip.toJson()],
    });
    return created['batch_id'].toString();
  }

  /// Headers with a fresh token for streaming the source video.
  Future<Map<String, String>> streamHeaders(String path) async {
    await _api.probe(path);
    return _api.authHeaders();
  }

  Uri resolve(String path) => _api.resolve(path);

  static List<ClipDraft> _clips(Map<String, dynamic> json) => [
        for (final clip in json['clips'] as List? ?? const [])
          if (clip is Map) ClipDraft.fromJson(Map<String, dynamic>.from(clip)),
      ];
}
