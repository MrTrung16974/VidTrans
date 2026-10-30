import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/websocket/job_events.dart';
import '../../data/branding_repository.dart';
import '../../domain/branding_models.dart';

/// While a logo job runs the list refreshes every 2 s, like web/js/branding.js.
const brandingPollInterval = Duration(seconds: 2);

final brandingSettingsProvider =
    AsyncNotifierProvider<BrandingSettingsController, BrandingSettings>(BrandingSettingsController.new);

class BrandingSettingsController extends AsyncNotifier<BrandingSettings> {
  BrandingRepository get _repo => ref.read(brandingRepositoryProvider);

  @override
  Future<BrandingSettings> build() => _repo.settings();

  Future<void> saveConfig(BrandingConfig config) async => state = AsyncData(await _repo.saveConfig(config));

  Future<void> uploadImage(String kind, String path) async => state = AsyncData(await _repo.uploadImage(kind, path));

  Future<void> deleteImage(String kind) async => state = AsyncData(await _repo.deleteImage(kind));
}

final brandingHistoryProvider =
    AsyncNotifierProvider.autoDispose<BrandingHistoryController, BrandingHistory>(BrandingHistoryController.new);

class BrandingHistoryController extends AutoDisposeAsyncNotifier<BrandingHistory> {
  BrandingRepository get _repo => ref.read(brandingRepositoryProvider);
  Timer? _poll;

  @override
  Future<BrandingHistory> build() async {
    ref.onDispose(() => _poll?.cancel());
    // Logo jobs are jobs too: a pushed change to one of them refreshes the list.
    final subscription = ref.read(jobEventsProvider).changes.listen((changes) {
      final ids = state.valueOrNull?.items.map((job) => job.id).toSet() ?? const <String>{};
      if (changes.resync || changes.items.any((item) => ids.contains(item['job_id']?.toString()))) refresh();
    });
    ref.onDispose(subscription.cancel);
    final history = await _repo.history();
    _schedule(history);
    return history;
  }

  void _schedule(BrandingHistory history) {
    _poll?.cancel();
    if (history.hasActive) _poll = Timer(brandingPollInterval, refresh);
  }

  Future<void> refresh() async {
    final next = await AsyncValue.guard(_repo.history);
    if (next.hasValue || state.valueOrNull == null) state = next;
    final value = next.valueOrNull ?? state.valueOrNull;
    if (value != null) _schedule(value);
  }
}

/// One logo job, re-fetched every 2 s while it is running.
final brandingJobProvider = StreamProvider.autoDispose.family<BrandingJob, String>((ref, jobId) async* {
  final repo = ref.read(brandingRepositoryProvider);
  var job = await repo.job(jobId);
  yield job;
  while (job.isActive) {
    await Future<void>.delayed(brandingPollInterval);
    job = await repo.job(jobId);
    yield job;
  }
});
