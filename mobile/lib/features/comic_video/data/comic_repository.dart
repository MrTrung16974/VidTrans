import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_client.dart';
import '../../../core/api/providers.dart';
import '../domain/comic_models.dart';
import '../domain/comic_options.dart';

final comicRepositoryProvider = Provider<ComicRepository>((ref) => ComicRepository(ref.watch(apiClientProvider)));

/// A local file picked by the user (story TXT/DOCX, music, picture).
class LocalFile {
  const LocalFile({required this.path, required this.name});

  final String path;
  final String name;

  Future<MultipartFile> toMultipart() => MultipartFile.fromFile(path, filename: name);
}

/// Scene page of the editor; the server returns the page holding a focused scene.
class ScenePage {
  const ScenePage({this.page = 1, this.limit = 6});

  final int page;
  final int limit;

  Map<String, dynamic> get query => {'scene_page': page, 'scene_limit': limit};
}

/// Every call of `/api/v1/comic`. Editing calls return the whole project
/// (the web replaces its state with it too).
class ComicRepository {
  ComicRepository(this._api);

  final ApiClient _api;

  static const _base = '/api/v1/comic';

  // ------------------------------------------------------------- creation
  Future<ComicOptions> options() async => ComicOptions.fromJson(await _api.get<Map<String, dynamic>>('$_base/options'));

  Future<Map<String, dynamic>> suggestBible(Map<String, String> fields, {LocalFile? file}) async {
    return _api.postMultipart<Map<String, dynamic>>('$_base/bible/suggest', await _form(fields, file: file));
  }

  /// New story (series); story text queues episode 1. Returns `{series_id, job_id?, episode_number?}`.
  Future<Map<String, dynamic>> createSeries(
    Map<String, String> fields, {
    LocalFile? file,
    LocalFile? music,
    String? idempotencyKey,
    void Function(int, int)? onProgress,
  }) async {
    return _api.postMultipart<Map<String, dynamic>>(
      '$_base/series',
      await _form(fields, file: file, music: music),
      idempotencyKey: idempotencyKey,
      onProgress: onProgress,
    );
  }

  /// A standalone video, or the next episode when `series_id` is set.
  Future<Map<String, dynamic>> createProject(
    Map<String, String> fields, {
    LocalFile? file,
    LocalFile? music,
    String? idempotencyKey,
    void Function(int, int)? onProgress,
  }) async {
    return _api.postMultipart<Map<String, dynamic>>(
      '$_base/projects',
      await _form(fields, file: file, music: music),
      idempotencyKey: idempotencyKey,
      onProgress: onProgress,
    );
  }

  Future<FormData> _form(Map<String, String> fields, {LocalFile? file, LocalFile? music}) async {
    return FormData.fromMap({
      ...fields,
      if (file != null) 'file': await file.toMultipart(),
      if (music != null) 'background_music': await music.toMultipart(),
    });
  }

  // --------------------------------------------------------------- lists
  Future<ComicProjectPage> projects({int page = 1, int limit = 20, String? search}) async {
    return ComicProjectPage.fromJson(await _api.get<Map<String, dynamic>>('$_base/projects', query: {
      'page': page,
      'limit': limit,
      if (search != null && search.isNotEmpty) 'search': search,
    }));
  }

  Future<List<SeriesSummary>> series() async {
    final data = await _api.get<Map<String, dynamic>>('$_base/series');
    return [
      for (final item in (data['items'] as List? ?? const []))
        if (item is Map) SeriesSummary.fromJson(Map<String, dynamic>.from(item)),
    ];
  }

  // -------------------------------------------------------------- series
  Future<SeriesDetail> seriesDetail(String id) async =>
      SeriesDetail.fromJson(await _api.get<Map<String, dynamic>>('$_base/series/$id'));

  Future<SeriesDetail> patchSeries(String id, Map<String, dynamic> changes) async =>
      SeriesDetail.fromJson(await _api.patch<Map<String, dynamic>>('$_base/series/$id', data: changes));

  Future<void> deleteSeries(String id, {bool withEpisodes = false}) =>
      _api.delete<dynamic>('$_base/series/$id${withEpisodes ? '?with_episodes=true' : ''}');

  Future<SeriesDetail> patchSeriesCharacter(String id, String characterId, Map<String, dynamic> changes) async =>
      SeriesDetail.fromJson(
          await _api.patch<Map<String, dynamic>>('$_base/series/$id/characters/$characterId', data: changes));

  Future<SeriesDetail> uploadSeriesReference(String id, String characterId, LocalFile picture) async =>
      SeriesDetail.fromJson(await _api.postMultipart<Map<String, dynamic>>(
          '$_base/series/$id/characters/$characterId/reference',
          FormData.fromMap({'file': await picture.toMultipart()})));

  Future<SeriesDetail> clearSeriesReference(String id, String characterId) async => SeriesDetail.fromJson(
      await _api.delete<Map<String, dynamic>>('$_base/series/$id/characters/$characterId/reference'));

  Future<SeriesDetail> patchSeriesLocation(String id, String locationId, Map<String, dynamic> changes) async =>
      SeriesDetail.fromJson(
          await _api.patch<Map<String, dynamic>>('$_base/series/$id/locations/$locationId', data: changes));

  // ------------------------------------------------------------- project
  String _project(String jobId) => '$_base/projects/$jobId';

  Future<ComicProject> project(String jobId, ScenePage page) async => ComicProject.fromJson(
      await _api.get<Map<String, dynamic>>(_project(jobId), query: page.query));

  Future<String> story(String jobId) async =>
      (await _api.get<Map<String, dynamic>>('${_project(jobId)}/story'))['text']?.toString() ?? '';

  Future<void> deleteProject(String jobId) => _api.delete<dynamic>(_project(jobId));

  /// Editing calls keep the scene page by sending it as a query, like `withSceneQuery`.
  Future<ComicProject> _edit(String method, String path, ScenePage page, {Object? data}) async {
    final query = page.query.entries.map((e) => '${e.key}=${e.value}').join('&');
    final url = '$path${path.contains('?') ? '&' : '?'}$query';
    final Map<String, dynamic> result = switch (method) {
      'PATCH' => await _api.patch<Map<String, dynamic>>(url, data: data ?? const {}),
      'DELETE' => await _api.delete<Map<String, dynamic>>(url),
      _ => data is FormData
          ? await _api.postMultipart<Map<String, dynamic>>(url, data)
          : await _api.post<Map<String, dynamic>>(url, data: data ?? const {}),
    };
    return ComicProject.fromJson(result);
  }

  Future<ComicProject> patchScene(String jobId, int scene, Map<String, dynamic> patch, ScenePage page) =>
      _edit('PATCH', '${_project(jobId)}/scenes/$scene', page, data: patch);

  /// Quick fix ("Vẽ lại", "Sai nhân vật", "Sai bối cảnh", "Sai trang phục").
  Future<ComicProject> fixScene(String jobId, int scene, Map<String, dynamic> fix, ScenePage page) =>
      _edit('POST', '${_project(jobId)}/scenes/$scene/fix', page, data: fix);

  Future<ComicProject> bulkScenes(String jobId, Map<String, dynamic> body, ScenePage page) =>
      _edit('POST', '${_project(jobId)}/scenes/bulk', page, data: body);

  Future<ComicProject> insertScene(String jobId,
          {required int after, required String narration, required String visualPrompt, required ScenePage page}) =>
      _edit('POST', '${_project(jobId)}/scenes', page,
          data: {'after': after, 'narration': narration, 'visual_prompt': visualPrompt});

  Future<ComicProject> deleteScene(String jobId, int scene, ScenePage page) =>
      _edit('DELETE', '${_project(jobId)}/scenes/$scene', page);

  Future<ComicProject> uploadSceneImage(String jobId, int scene, LocalFile picture, ScenePage page) async =>
      _edit('POST', '${_project(jobId)}/scenes/$scene/image', page,
          data: FormData.fromMap({'file': await picture.toMultipart()}));

  Future<ComicProject> clearSceneImage(String jobId, int scene, ScenePage page) =>
      _edit('DELETE', '${_project(jobId)}/scenes/$scene/image', page);

  Future<ComicProject> patchCharacter(String jobId, String id, Map<String, dynamic> changes, ScenePage page) =>
      _edit('PATCH', '${_project(jobId)}/characters/$id', page, data: changes);

  Future<ComicProject> patchLocation(String jobId, String id, Map<String, dynamic> changes, ScenePage page) =>
      _edit('PATCH', '${_project(jobId)}/locations/$id', page, data: changes);

  // ---------------------------------------------------------------- shots
  String _shot(String jobId, int scene, int shot) => '${_project(jobId)}/scenes/$scene/shots/$shot';

  Future<ComicProject> patchShot(String jobId, int scene, int shot, Map<String, dynamic> patch, ScenePage page) =>
      _edit('PATCH', _shot(jobId, scene, shot), page, data: patch);

  Future<ComicProject> insertShot(String jobId, int scene,
          {required int after, required String keyframePrompt, String narration = '', required ScenePage page}) =>
      _edit('POST', '${_project(jobId)}/scenes/$scene/shots', page,
          data: {'after': after, 'keyframe_prompt': keyframePrompt, 'narration': narration});

  Future<ComicProject> deleteShot(String jobId, int scene, int shot, ScenePage page) =>
      _edit('DELETE', _shot(jobId, scene, shot), page);

  Future<ComicProject> regenerateShot(String jobId, int scene, int shot, ScenePage page) =>
      _edit('POST', '${_shot(jobId, scene, shot)}/regenerate', page);

  Future<ComicProject> uploadKeyframe(String jobId, int scene, int shot, LocalFile picture, ScenePage page) async =>
      _edit('POST', '${_shot(jobId, scene, shot)}/keyframe', page,
          data: FormData.fromMap({'file': await picture.toMultipart()}));

  Future<ComicProject> clearKeyframe(String jobId, int scene, int shot, ScenePage page) =>
      _edit('DELETE', '${_shot(jobId, scene, shot)}/keyframe', page);

  // ----------------------------------------------------------- references
  Future<ReferenceList> characterReferences(String jobId, String id) async => ReferenceList.fromJson(
      await _api.get<Map<String, dynamic>>('${_project(jobId)}/characters/$id/references'));

  Future<ComicProject> uploadCharacterReference(String jobId, String id, LocalFile picture, ScenePage page) async =>
      _edit('POST', '${_project(jobId)}/characters/$id/references', page,
          data: FormData.fromMap({'file': await picture.toMultipart()}));

  Future<ComicProject> approveCharacterReference(String jobId, String id, String name, ScenePage page) =>
      _edit('POST', '${_project(jobId)}/characters/$id/references/${Uri.encodeComponent(name)}/approve', page);

  Future<ComicProject> unpinCharacterReference(String jobId, String id, ScenePage page) =>
      _edit('DELETE', '${_project(jobId)}/characters/$id/references/approval', page);

  Future<ReferenceList> locationReferences(String jobId, String id) async => ReferenceList.fromJson(
      await _api.get<Map<String, dynamic>>('${_project(jobId)}/locations/$id/references'));

  Future<ComicProject> uploadLocationReference(String jobId, String id, LocalFile picture, ScenePage page) async =>
      _edit('POST', '${_project(jobId)}/locations/$id/references', page,
          data: FormData.fromMap({'file': await picture.toMultipart()}));

  Future<ComicProject> approveLocationReference(String jobId, String id, String name, ScenePage page) =>
      _edit('POST', '${_project(jobId)}/locations/$id/references/${Uri.encodeComponent(name)}/approve', page);

  Future<ComicProject> unpinLocationReference(String jobId, String id, ScenePage page) =>
      _edit('DELETE', '${_project(jobId)}/locations/$id/references/approval', page);

  // --------------------------------------------------------------- render
  /// [body]: a [RenderRequest] body. The same [idempotencyKey] must be reused
  /// when retrying one click, so a flaky network cannot queue (and bill) twice.
  Future<Map<String, dynamic>> render(String jobId, Map<String, dynamic> body, {required String idempotencyKey}) =>
      _api.post<Map<String, dynamic>>('${_project(jobId)}/render',
          data: body, options: Options(headers: {'Idempotency-Key': idempotencyKey}));

  Future<void> cancel(String jobId) => _api.post<dynamic>('/api/v1/jobs/$jobId/cancel');

  // ---------------------------------------------------------------- media
  Uri mediaUri(String path) => _api.resolve(path);

  Future<Map<String, String>> authHeaders() => _api.authHeaders();
}

/// Bodies for `POST .../render`, one per editor button (web `data-comic-action`).
class RenderRequest {
  static Map<String, dynamic> render() => {'images_only': false, 'reanalyze': false};

  /// "Vẽ thử 3 cảnh": references and the first three scenes only.
  static Map<String, dynamic> trial() => {'images_only': true, 'reanalyze': false, 'preview_scenes': 3};

  /// "Chỉ sinh ảnh" / "Chỉ sinh keyframe".
  static Map<String, dynamic> imagesOnly() => {'images_only': true, 'reanalyze': false};

  /// "Phân tích lại truyện": replaces the whole storyboard.
  static Map<String, dynamic> reanalyze() => {'images_only': false, 'reanalyze': true};

  /// "Xem trước shot" (animated storyboards), label "scene.shot".
  static Map<String, dynamic> shot(String label) => {'shot': label};
}
