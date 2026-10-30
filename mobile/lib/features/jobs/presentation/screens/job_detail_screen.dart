import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../data/jobs_repository.dart';
import '../../domain/job.dart';
import '../providers/jobs_providers.dart';
import '../widgets/job_downloads.dart';
import '../widgets/job_status_chip.dart';

class JobDetailScreen extends ConsumerWidget {
  const JobDetailScreen({super.key, required this.jobId});

  final String jobId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final job = ref.watch(jobDetailProvider(jobId));
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('Chi tiết công việc')),
      body: job.when(
        loading: () => const Center(
          child: CircularProgressIndicator(color: AppColors.brand),
        ),
        error: (error, _) => ErrorView(
          message: errorMessage(error),
          onRetry: () => ref.invalidate(jobDetailProvider(jobId)),
        ),
        data: (job) => _JobDetail(job: job),
      ),
    );
  }
}

class _JobDetail extends ConsumerWidget {
  const _JobDetail({required this.job});

  final Job job;

  Future<void> _run(
    BuildContext context,
    WidgetRef ref,
    Future<void> Function() action,
    String done,
  ) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await action();
      messenger.showSnackBar(SnackBar(content: Text(done)));
      ref.invalidate(jobDetailProvider(job.id));
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final repo = ref.read(jobsRepositoryProvider);
    final readOnly = ref.watch(sessionProvider).valueOrNull?.isReadOnly ?? true;

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
      children: [
        StudioCard(
          topAccentColor: AppColors.brand,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 44,
                    height: 44,
                    decoration: BoxDecoration(
                      color: AppColors.brandSoft,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: AppColors.brandBorder),
                    ),
                    alignment: Alignment.center,
                    child: const Icon(
                      Icons.movie_filter_rounded,
                      color: AppColors.brand,
                      size: 23,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          job.title,
                          style: const TextStyle(
                            fontSize: 16.5,
                            fontWeight: FontWeight.w800,
                            color: AppColors.ink,
                            letterSpacing: -0.2,
                          ),
                        ),
                        const SizedBox(height: 8),
                        Wrap(
                          spacing: 8,
                          runSpacing: 6,
                          crossAxisAlignment: WrapCrossAlignment.center,
                          children: [
                            JobStatusChip(job.status),
                            if (job.queuePosition != null)
                              StudioEyebrow(
                                label: 'Vị trí hàng đợi #${job.queuePosition}',
                              ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              if (job.isActive) ...[
                const SizedBox(height: 16),
                ClipRRect(
                  borderRadius: BorderRadius.circular(999),
                  child: LinearProgressIndicator(
                    value: job.progress,
                    minHeight: 8,
                  ),
                ),
                const SizedBox(height: 8),
                Text(
                  [job.step, job.stepDetail].whereType<String>().join(' — '),
                  style: const TextStyle(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w600,
                    color: AppColors.inkSoft,
                  ),
                ),
              ],
            ],
          ),
        ),
        if (job.error != null) ...[
          const SizedBox(height: 14),
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: AppColors.redSoft,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: AppColors.redBorder),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Icon(
                  Icons.error_outline_rounded,
                  color: AppColors.red,
                  size: 20,
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    job.error!,
                    style: const TextStyle(
                      color: AppColors.redText,
                      fontWeight: FontWeight.w600,
                      fontSize: 13,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
        if (job.videoUrl != null) ...[
          const SizedBox(height: 14),
          Container(
            padding: const EdgeInsets.all(18),
            decoration: BoxDecoration(
              gradient: AppColors.darkStageGradient,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: const Color(0xFF334155)),
              boxShadow: AppColors.cardShadow,
            ),
            child: Column(
              children: [
                Container(
                  width: 52,
                  height: 52,
                  decoration: const BoxDecoration(
                    gradient: AppColors.brandGradient,
                    shape: BoxShape.circle,
                    boxShadow: AppColors.brandGlowShadow,
                  ),
                  alignment: Alignment.center,
                  child: const Icon(
                    Icons.play_arrow_rounded,
                    color: Colors.white,
                    size: 30,
                  ),
                ),
                const SizedBox(height: 10),
                const Text(
                  'Video kết quả đã sẵn sàng',
                  style: TextStyle(
                    fontSize: 15.5,
                    fontWeight: FontWeight.w800,
                    color: Colors.white,
                  ),
                ),
                const SizedBox(height: 14),
                StudioGradientButton(
                  onPressed: () => context.push('/jobs/${job.id}/player'),
                  icon: Icons.play_circle_outline_rounded,
                  label: 'Xem video kết quả',
                ),
                if (job.status == 'completed' && !readOnly) ...[
                  const SizedBox(height: 10),
                  OutlinedButton.icon(
                    style: OutlinedButton.styleFrom(
                      foregroundColor: Colors.white,
                      side: const BorderSide(color: Color(0xFF475569)),
                    ),
                    onPressed: () => context.push('/social/publish/${job.id}'),
                    icon: const Icon(Icons.share_rounded),
                    label: const Text('Đăng mạng xã hội'),
                  ),
                ],
              ],
            ),
          ),
        ],
        JobDownloads(job: job),
        if (!readOnly) ...[
          const SizedBox(height: 16),
          if (job.canCancel)
            OutlinedButton.icon(
              onPressed: () => _run(
                context,
                ref,
                () => repo.cancel(job.id),
                'Đã gửi yêu cầu hủy',
              ),
              icon: const Icon(
                Icons.stop_circle_outlined,
                color: AppColors.red,
              ),
              label: const Text(
                'Hủy công việc',
                style: TextStyle(color: AppColors.red),
              ),
              style: OutlinedButton.styleFrom(
                side: const BorderSide(color: AppColors.redBorder),
                backgroundColor: AppColors.redSoft,
              ),
            ),
          if (job.canRetry) ...[
            const SizedBox(height: 10),
            FilledButton.tonalIcon(
              onPressed: () => _run(
                context,
                ref,
                () => repo.retry(job.id),
                'Đã đưa vào hàng đợi lại',
              ),
              icon: const Icon(Icons.refresh_rounded),
              label: const Text('Chạy lại'),
            ),
          ],
        ],
      ],
    );
  }
}
