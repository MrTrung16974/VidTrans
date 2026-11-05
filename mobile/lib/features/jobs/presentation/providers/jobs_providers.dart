import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/websocket/job_events.dart';
import '../../data/jobs_repository.dart';
import '../../domain/bulk_actions.dart';
import '../../domain/job.dart';
import '../../domain/job_list.dart';

final jobStatusFilterProvider = StateProvider<String?>((ref) => null);

/// Server-side search on the jobs page (name or job id), as on the web.
final jobSearchProvider = StateProvider<String>((ref) => '');

/// 1-based page of the jobs list. Reset to 1 when the filter, search or size changes.
final jobPageProvider = StateProvider<int>((ref) => 1);

const jobPageSizes = [10, 20, 50, 100];
final jobPageSizeProvider = StateProvider<int>((ref) => 10);

final jobListProvider =
    AsyncNotifierProvider.autoDispose<JobListController, JobList>(
      JobListController.new,
    );

/// The selected page from REST, then patched in place by WebSocket change sets.
class JobListController extends AutoDisposeAsyncNotifier<JobList> {
  JobsRepository get _repo => ref.read(jobsRepositoryProvider);

  @override
  Future<JobList> build() async {
    ref.watch(jobStatusFilterProvider);
    ref.watch(jobSearchProvider);
    ref.watch(jobPageProvider);
    ref.watch(jobPageSizeProvider);
    final subscription = ref.read(jobEventsProvider).changes.listen(_onChanges);
    ref.onDispose(subscription.cancel);
    return _fetch();
  }

  /// The page, size, filter and search a request was made for.
  (int, int, String?, String) _view() => (
    ref.read(jobPageProvider),
    ref.read(jobPageSizeProvider),
    ref.read(jobStatusFilterProvider),
    ref.read(jobSearchProvider).trim(),
  );

  Future<JobList> _fetch() async {
    final list = await _fetchPage();
    // The server moves a page past the end (jobs deleted) back to the last one; follow
    // it, so tapping that page number again is not a no-op on a stale value.
    if (list.page != ref.read(jobPageProvider)) {
      Future.microtask(
        () => ref.read(jobPageProvider.notifier).state = list.page,
      );
    }
    return list;
  }

  Future<JobList> _fetchPage() async {
    final page = ref.read(jobPageProvider);
    final list = await _repo.list(
      page: page,
      limit: ref.read(jobPageSizeProvider),
      status: ref.read(jobStatusFilterProvider),
      search: ref.read(jobSearchProvider).trim(),
    );
    // The last page emptied (jobs deleted or filtered away): show the new last page.
    if (list.jobs.isEmpty && page > 1 && list.totalPages < page) {
      final last = list.totalPages < 1 ? 1 : list.totalPages;
      return _repo.list(
        page: last,
        limit: ref.read(jobPageSizeProvider),
        status: ref.read(jobStatusFilterProvider),
        search: ref.read(jobSearchProvider).trim(),
      );
    }
    return list;
  }

  void _onChanges(JobChanges changes) {
    final current = state.valueOrNull;
    if (current == null) return;
    final result = applyJobChanges(
      current.jobs,
      changes,
      statusFilter: ref.read(jobStatusFilterProvider),
    );
    final before = {for (final job in current.jobs) job.id: job.status};
    // A status change moves the per-status counts of every page: fetch them again.
    final statusChanged = result.jobs.any(
      (job) => before[job.id] != job.status,
    );
    state = AsyncData(current.copyWith(jobs: result.jobs));
    if (result.reload || statusChanged) unawaited(refresh());
  }

  Future<void> refresh() async {
    final view = _view();
    final next = await AsyncValue.guard(_fetch);
    // The user moved to another page, size, filter or search meanwhile: that view is being
    // built already, and this older result must not replace it.
    if (view != _view()) return;
    // Keep showing the old list if a background reload fails.
    if (next.hasValue || state.valueOrNull == null) state = next;
  }

  /// Runs [action] on [jobs], a few requests at a time, then reloads the list.
  /// One failing job does not stop the others.
  Future<BulkJobResult> runBulk(BulkJobAction action, List<Job> jobs) async {
    const concurrency = 4;
    final succeeded = <String>[];
    final errors = <String>[];
    Future<void> one(Job job) async {
      try {
        await switch (action) {
          BulkJobAction.pause => _repo.pause(job.id),
          BulkJobAction.resume => _repo.resume(job.id),
          BulkJobAction.cancel => _repo.cancel(job.id),
          BulkJobAction.retry => _repo.retry(job.id),
          BulkJobAction.delete => _repo.delete(job.id),
        };
        succeeded.add(job.id);
      } catch (error) {
        errors.add(errorMessage(error));
      }
    }

    for (var start = 0; start < jobs.length; start += concurrency) {
      await Future.wait(jobs.skip(start).take(concurrency).map(one));
    }
    await refresh();
    return BulkJobResult(action, succeeded: succeeded, errors: errors);
  }
}

/// Job detail: fetched by REST, kept fresh by the same event stream.
final jobDetailProvider = StreamProvider.autoDispose.family<Job, String>((
  ref,
  jobId,
) async* {
  final repo = ref.read(jobsRepositoryProvider);
  final events = ref.read(jobEventsProvider).changes;
  var job = await repo.get(jobId);
  yield job;
  await for (final changes in events) {
    if (changes.resync) {
      job = await repo.get(jobId);
      yield job;
      continue;
    }
    for (final item in changes.items) {
      if (item['job_id']?.toString() == jobId) {
        job = Job.fromJson(item);
        yield job;
      }
    }
    final position = changes.queuePositions?[jobId];
    if (changes.queuePositions != null && position != job.queuePosition) {
      job = job.withQueuePosition(position);
      yield job;
    }
  }
});
