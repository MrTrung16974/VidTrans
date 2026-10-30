import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../../home/presentation/screens/home_shell.dart';
import '../../domain/job.dart';
import '../providers/jobs_providers.dart';
import '../widgets/job_status_chip.dart';

class JobsScreen extends ConsumerWidget {
  const JobsScreen({super.key});

  static const _filters = <String?>[
    null,
    'processing',
    'queued',
    'completed',
    'failed',
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final jobs = ref.watch(jobListProvider);
    final filter = ref.watch(jobStatusFilterProvider);

    final allJobs = jobs.valueOrNull?.jobs ?? const <Job>[];
    final processingCount =
        allJobs.where((j) => j.status == 'processing').length;
    final queuedCount = allJobs
        .where((j) => j.status == 'queued' || j.status == 'scheduled')
        .length;
    final completedCount = allJobs.where((j) => j.status == 'completed').length;
    final failedCount = allJobs
        .where((j) => j.status == 'failed' || j.status == 'cancelled')
        .length;

    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: StudioTopBar(
        extraActions: [
          IconButton(
            icon: const Icon(Icons.refresh_rounded, size: 20),
            tooltip: 'Làm mới',
            onPressed: () => ref.read(jobListProvider.notifier).refresh(),
          ),
          const SizedBox(width: 4),
        ],
      ),
      body: Column(
        children: [
          Container(
            decoration: const BoxDecoration(
              color: AppColors.surface,
              border: Border(bottom: BorderSide(color: AppColors.line)),
            ),
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
            child: Column(
              children: [
                Row(
                  children: [
                    Expanded(
                      child: StudioMetricCard(
                        label: 'Đang chạy',
                        value: '$processingCount',
                        accentColor: AppColors.blue,
                        backgroundColor: processingCount > 0
                            ? AppColors.blueTint
                            : AppColors.surfaceSubtle,
                        borderColor: processingCount > 0
                            ? AppColors.blueBorder
                            : AppColors.line,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: StudioMetricCard(
                        label: 'Hàng chờ',
                        value: '$queuedCount',
                        accentColor: AppColors.amber,
                        backgroundColor: AppColors.surfaceSubtle,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: StudioMetricCard(
                        label: 'Hoàn tất',
                        value: '$completedCount',
                        accentColor: AppColors.green,
                        backgroundColor: AppColors.surfaceSubtle,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: StudioMetricCard(
                        label: 'Thất bại',
                        value: '$failedCount',
                        accentColor: AppColors.red,
                        backgroundColor: failedCount > 0
                            ? AppColors.redTint
                            : AppColors.surfaceSubtle,
                        borderColor: failedCount > 0
                            ? AppColors.redBorder
                            : AppColors.line,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                SizedBox(
                  height: 38,
                  child: ListView(
                    scrollDirection: Axis.horizontal,
                    children: [
                      for (final status in _filters)
                        Padding(
                          padding: const EdgeInsets.only(right: 8),
                          child: ChoiceChip(
                            showCheckmark: false,
                            label: Text(
                              status == null
                                  ? 'Tất cả'
                                  : jobStatusLabels[status]!,
                            ),
                            selected: filter == status,
                            onSelected: (_) => ref
                                .read(jobStatusFilterProvider.notifier)
                                .state = status,
                          ),
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          Expanded(
            child: jobs.when(
              loading: () => const Center(
                child: CircularProgressIndicator(color: AppColors.brand),
              ),
              error: (error, _) => ErrorView(
                message: errorMessage(error),
                onRetry: () => ref.invalidate(jobListProvider),
              ),
              data: (list) => RefreshIndicator(
                color: AppColors.brand,
                onRefresh: () => ref.read(jobListProvider.notifier).refresh(),
                child: list.jobs.isEmpty
                    ? ListView(
                        children: [
                          const SizedBox(height: 36),
                          StudioEmptyState(
                            icon: Icons.inbox_outlined,
                            title: 'Chưa có công việc nào',
                            subtitle:
                                'Chuyển sang tab Dịch video để tải video lên hoặc dán link TikTok/Douyin để tự động xử lý.',
                            action: StudioGradientButton(
                              expanded: false,
                              icon: Icons.add_circle_outline_rounded,
                              label: 'Tạo công việc mới',
                              onPressed: () => context.go('/translate'),
                            ),
                          ),
                        ],
                      )
                    : NotificationListener<ScrollNotification>(
                        onNotification: (notification) {
                          if (notification.metrics.extentAfter < 400) {
                            ref.read(jobListProvider.notifier).loadMore();
                          }
                          return false;
                        },
                        child: ListView.separated(
                          padding: const EdgeInsets.fromLTRB(16, 14, 16, 28),
                          itemCount: list.jobs.length + (list.hasMore ? 1 : 0),
                          separatorBuilder: (_, _) =>
                              const SizedBox(height: 10),
                          itemBuilder: (context, index) {
                            if (index >= list.jobs.length) {
                              return const Padding(
                                padding: EdgeInsets.all(16),
                                child: Center(
                                  child: CircularProgressIndicator(
                                    color: AppColors.brand,
                                  ),
                                ),
                              );
                            }
                            return _JobTile(job: list.jobs[index]);
                          },
                        ),
                      ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _JobTile extends StatelessWidget {
  const _JobTile({required this.job});

  final Job job;

  Color _accentForStatus(String status) {
    return switch (status) {
      'completed' => AppColors.green,
      'processing' => AppColors.blue,
      'queued' || 'cancelling' => AppColors.amber,
      'scheduled' => AppColors.teal,
      'failed' || 'cancelled' => AppColors.red,
      _ => AppColors.muted,
    };
  }

  @override
  Widget build(BuildContext context) {
    final created = job.createdAt;
    final subtitle = [
      if (job.queuePosition != null) 'Vị trí hàng đợi #${job.queuePosition}',
      if (job.isActive && job.step != null) job.step!,
      if (created != null) DateFormat('dd/MM HH:mm').format(created),
    ].join(' · ');
    final accent = _accentForStatus(job.status);

    return StudioCard(
      padding: const EdgeInsets.all(14),
      borderColor:
          job.status == 'processing' ? AppColors.blueBorder : AppColors.line,
      onTap: () => context.push('/jobs/${job.id}'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(
                  color: accent.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(11),
                  border: Border.all(color: accent.withValues(alpha: 0.24)),
                ),
                alignment: Alignment.center,
                child: Icon(
                  job.status == 'completed'
                      ? Icons.play_circle_fill_rounded
                      : job.status == 'failed'
                          ? Icons.warning_amber_rounded
                          : Icons.movie_filter_rounded,
                  size: 21,
                  color: accent,
                ),
              ),
              const SizedBox(width: 11),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      job.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 14.5,
                        fontWeight: FontWeight.w700,
                        color: AppColors.ink,
                        letterSpacing: -0.2,
                      ),
                    ),
                    if (subtitle.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text(
                        subtitle,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 12,
                          color: AppColors.muted,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(width: 8),
              JobStatusChip(job.status),
            ],
          ),
          if (job.status == 'processing') ...[
            const SizedBox(height: 10),
            ClipRRect(
              borderRadius: BorderRadius.circular(999),
              child: LinearProgressIndicator(
                value: job.progress,
                minHeight: 6,
                backgroundColor: AppColors.line,
                color: AppColors.brand,
              ),
            ),
          ],
        ],
      ),
    );
  }
}
