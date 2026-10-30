import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/deeplink/deep_link_service.dart';
import '../../../jobs/data/jobs_repository.dart';
import '../../../jobs/domain/job.dart';
import '../../data/social_repository.dart';
import '../../domain/social_models.dart';

/// Connection state of one platform; reloaded when an OAuth flow returns to the app.
final socialAuthProvider = FutureProvider.autoDispose.family<SocialAuthStatus, SocialPlatform>((ref, platform) {
  final subscription = ref.watch(deepLinkServiceProvider).links.listen((link) {
    if (link is OAuthResult && link.platform == platform.slug) ref.invalidateSelf();
  });
  ref.onDispose(subscription.cancel);
  return ref.watch(socialRepositoryProvider).authStatus(platform);
});

/// Finished videos that can be published.
final publishableJobsProvider = FutureProvider.autoDispose<List<Job>>((ref) async {
  final page = await ref.watch(jobsRepositoryProvider).list(status: 'completed', limit: 50);
  return page.jobs.where((job) => job.videoUrl != null).toList();
});

final socialDraftProvider = FutureProvider.autoDispose.family<SocialDraft, String>(
  (ref, jobId) => ref.watch(socialRepositoryProvider).draft(jobId),
);

final publishAttemptsProvider = FutureProvider.autoDispose.family<List<PublishAttempt>, String>(
  (ref, jobId) => ref.watch(socialRepositoryProvider).attempts(jobId),
);
