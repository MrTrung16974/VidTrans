import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/websocket/job_events.dart';
import '../../data/jobs_repository.dart';
import '../../domain/job.dart';
import '../../domain/job_list.dart';

final jobStatusFilterProvider = StateProvider<String?>((ref) => null);

final jobListProvider = AsyncNotifierProvider.autoDispose<JobListController, JobList>(JobListController.new);

/// First page from REST, then patched in place by WebSocket change sets.
class JobListController extends AutoDisposeAsyncNotifier<JobList> {
  JobsRepository get _repo => ref.read(jobsRepositoryProvider);
  bool _loadingMore = false;

  @override
  Future<JobList> build() async {
    final status = ref.watch(jobStatusFilterProvider);
    final subscription = ref.read(jobEventsProvider).changes.listen(_onChanges);
    ref.onDispose(subscription.cancel);
    return _repo.list(status: status);
  }

  void _onChanges(JobChanges changes) {
    final current = state.valueOrNull;
    if (current == null) return;
    final result = applyJobChanges(current.jobs, changes, statusFilter: ref.read(jobStatusFilterProvider));
    state = AsyncData(current.copyWith(jobs: result.jobs));
    if (result.reload) unawaited(refresh());
  }

  Future<void> refresh() async {
    final status = ref.read(jobStatusFilterProvider);
    final next = await AsyncValue.guard(() => _repo.list(status: status));
    // Keep showing the old list if a background reload fails.
    if (next.hasValue || state.valueOrNull == null) state = next;
  }

  Future<void> loadMore() async {
    final current = state.valueOrNull;
    if (current == null || !current.hasMore || _loadingMore) return;
    _loadingMore = true;
    try {
      final next = await _repo.list(page: current.page + 1, status: ref.read(jobStatusFilterProvider));
      final seen = current.jobs.map((job) => job.id).toSet();
      state = AsyncData(next.copyWith(jobs: [...current.jobs, ...next.jobs.where((j) => !seen.contains(j.id))]));
    } finally {
      _loadingMore = false;
    }
  }
}

/// Job detail: fetched by REST, kept fresh by the same event stream.
final jobDetailProvider = StreamProvider.autoDispose.family<Job, String>((ref, jobId) async* {
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
