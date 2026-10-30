import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../../home/presentation/screens/home_shell.dart';
import '../../domain/social_models.dart';
import '../providers/social_providers.dart';
import '../widgets/social_widgets.dart';

/// Social publishing home: connected accounts, then pick a finished video.
class SocialHubScreen extends ConsumerWidget {
  const SocialHubScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final jobs = ref.watch(publishableJobsProvider);

    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: const StudioTopBar(),
      body: RefreshIndicator(
        onRefresh: () async {
          for (final platform in SocialPlatform.all) {
            ref.invalidate(socialAuthProvider(platform));
          }
          ref.invalidate(publishableJobsProvider);
          await ref.read(publishableJobsProvider.future).catchError((_) => const <Never>[]);
        },
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 108),
          children: [
            const StudioStepHeader(
              stepNumber: 1,
              icon: Icons.link_rounded,
              title: 'Kết nối tài khoản',
              subtitle: 'Đăng nhập nền tảng một lần, VidTrans sẽ đăng video thay bạn',
            ),
            const SizedBox(height: 12),
            for (final platform in SocialPlatform.all) PlatformConnectionCard(platform: platform),
            const SizedBox(height: 12),
            const StudioStepHeader(
              stepNumber: 2,
              icon: Icons.video_library_rounded,
              title: 'Chọn video để đăng',
              subtitle: 'Chỉ video đã xử lý xong mới đăng được',
            ),
            const SizedBox(height: 12),
            jobs.when(
              loading: () => const Padding(
                padding: EdgeInsets.all(24),
                child: Center(child: CircularProgressIndicator()),
              ),
              error: (error, _) => ErrorView(
                message: errorMessage(error),
                onRetry: () => ref.invalidate(publishableJobsProvider),
              ),
              data: (items) => items.isEmpty
                  ? const StudioEmptyState(
                      icon: Icons.movie_filter_outlined,
                      title: 'Chưa có video hoàn thành',
                      subtitle: 'Video dịch, truyện tranh hay logo xử lý xong sẽ hiện ở đây.',
                    )
                  : Column(
                      children: [
                        for (final job in items)
                          StudioCard(
                            margin: const EdgeInsets.only(bottom: 10),
                            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                            onTap: () => context.push('/social/publish/${job.id}'),
                            child: Row(
                              children: [
                                const Icon(Icons.play_circle_outline_rounded, color: AppColors.brand),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text(job.title, maxLines: 1, overflow: TextOverflow.ellipsis,
                                          style: Theme.of(context).textTheme.titleSmall),
                                      if (job.createdAt != null)
                                        Text(DateFormat('dd/MM/yyyy HH:mm').format(job.createdAt!),
                                            style: Theme.of(context).textTheme.bodySmall),
                                    ],
                                  ),
                                ),
                                const Icon(Icons.chevron_right_rounded, color: AppColors.muted),
                              ],
                            ),
                          ),
                      ],
                    ),
            ),
          ],
        ),
      ),
    );
  }
}
