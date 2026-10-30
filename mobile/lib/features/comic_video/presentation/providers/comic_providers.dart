import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/websocket/job_events.dart';
import '../../data/comic_repository.dart';
import '../../domain/comic_models.dart';
import '../../domain/comic_options.dart';

const comicJobAction = 'comic_video';

/// How long to coalesce job events before reloading (LIVE_REFRESH_MS on the web).
const _liveRefresh = Duration(milliseconds: 800);

final comicOptionsProvider = FutureProvider<ComicOptions>((ref) => ref.watch(comicRepositoryProvider).options());

bool _touchesComic(JobChanges changes, bool Function(String jobId) watched) =>
    changes.resync ||
    changes.removed.any(watched) ||
    changes.items.any((item) => item['job_action'] == comicJobAction || watched(item['job_id']?.toString() ?? ''));

/// Reloads [reload] at most once per [_liveRefresh] while events keep coming.
class _LiveReload {
  _LiveReload(this.reload);

  final Future<void> Function() reload;
  Timer? _timer;

  void schedule() {
    _timer ??= Timer(_liveRefresh, () {
      _timer = null;
      unawaited(reload());
    });
  }

  void dispose() => _timer?.cancel();
}

// ------------------------------------------------------------- videos list
final comicProjectsProvider =
    AsyncNotifierProvider.autoDispose<ComicProjectsController, ComicProjectPage>(ComicProjectsController.new);

class ComicProjectsController extends AutoDisposeAsyncNotifier<ComicProjectPage> {
  ComicRepository get _repo => ref.read(comicRepositoryProvider);
  bool _loadingMore = false;

  @override
  Future<ComicProjectPage> build() async {
    final live = _LiveReload(refresh);
    final subscription = ref.read(jobEventsProvider).changes.listen((changes) {
      final ids = state.valueOrNull?.items.map((job) => job.id).toSet() ?? const <String>{};
      if (_touchesComic(changes, ids.contains)) live.schedule();
    });
    ref.onDispose(() {
      subscription.cancel();
      live.dispose();
    });
    return _repo.projects();
  }

  Future<void> refresh() async {
    final next = await AsyncValue.guard(() => _repo.projects());
    if (next.hasValue || state.valueOrNull == null) state = next;
  }

  Future<void> loadMore() async {
    final current = state.valueOrNull;
    if (current == null || !current.hasMore || _loadingMore) return;
    _loadingMore = true;
    try {
      final next = await _repo.projects(page: current.page + 1);
      final seen = current.items.map((job) => job.id).toSet();
      state = AsyncData(ComicProjectPage(
        items: [...current.items, ...next.items.where((job) => !seen.contains(job.id))],
        page: next.page,
        totalPages: next.totalPages,
        total: next.total,
      ));
    } finally {
      _loadingMore = false;
    }
  }
}

// ------------------------------------------------------------- series list
final comicSeriesListProvider = FutureProvider.autoDispose<List<SeriesSummary>>((ref) {
  return ref.watch(comicRepositoryProvider).series();
});

// ------------------------------------------------------------ one series
final seriesDetailProvider =
    AsyncNotifierProvider.autoDispose.family<SeriesController, SeriesDetail, String>(SeriesController.new);

class SeriesController extends AutoDisposeFamilyAsyncNotifier<SeriesDetail, String> {
  ComicRepository get _repo => ref.read(comicRepositoryProvider);

  @override
  Future<SeriesDetail> build(String seriesId) async {
    final live = _LiveReload(reload);
    final subscription = ref.read(jobEventsProvider).changes.listen((changes) {
      final ids = state.valueOrNull?.episodes.map((job) => job.id).toSet() ?? const <String>{};
      if (changes.resync || changes.items.any((item) => ids.contains(item['job_id']?.toString()))) {
        live.schedule();
      }
    });
    ref.onDispose(() {
      subscription.cancel();
      live.dispose();
    });
    return _repo.seriesDetail(seriesId);
  }

  Future<void> reload() async {
    final next = await AsyncValue.guard(() => _repo.seriesDetail(arg));
    if (next.hasValue || state.valueOrNull == null) state = next;
  }

  /// Runs an edit returning the new series and shows it.
  Future<void> apply(Future<SeriesDetail> edit) async {
    state = AsyncData(await edit);
    ref.invalidate(comicSeriesListProvider);
  }
}

// ------------------------------------------------------------ episode editor
final comicProjectProvider =
    AsyncNotifierProvider.autoDispose.family<ComicProjectController, ComicProject, String>(ComicProjectController.new);

/// One project in the editor: the current scene page, refreshed while the
/// job runs and replaced by the response of every edit.
class ComicProjectController extends AutoDisposeFamilyAsyncNotifier<ComicProject, String> {
  ComicRepository get _repo => ref.read(comicRepositoryProvider);

  ScenePage page = const ScenePage();

  @override
  Future<ComicProject> build(String jobId) async {
    final live = _LiveReload(reload);
    final subscription = ref.read(jobEventsProvider).changes.listen((changes) {
      if (changes.resync || changes.items.any((item) => item['job_id']?.toString() == jobId)) live.schedule();
    });
    ref.onDispose(() {
      subscription.cancel();
      live.dispose();
    });
    final project = await _repo.project(jobId, page);
    _syncPage(project);
    return project;
  }

  void _syncPage(ComicProject project) {
    page = ScenePage(page: project.pagination.page, limit: page.limit);
  }

  Future<void> reload() async {
    final next = await AsyncValue.guard(() => _repo.project(arg, page));
    if (next.hasValue) _syncPage(next.requireValue);
    if (next.hasValue || state.valueOrNull == null) state = next;
  }

  Future<void> goToPage(int number, {int? limit}) async {
    page = ScenePage(page: number, limit: limit ?? page.limit);
    await reload();
  }

  /// Runs an edit (which returns the whole project) and shows the result.
  Future<void> apply(Future<ComicProject> Function(ScenePage page) edit) async {
    final project = await edit(page);
    _syncPage(project);
    state = AsyncData(project);
  }
}

final characterReferencesProvider =
    FutureProvider.autoDispose.family<ReferenceList, ({String jobId, String characterId})>((ref, key) {
  // Re-read whenever the project changes (a new variant was drawn or approved).
  ref.watch(comicProjectProvider(key.jobId));
  return ref.read(comicRepositoryProvider).characterReferences(key.jobId, key.characterId);
});
