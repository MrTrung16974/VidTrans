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

class JobsScreen extends ConsumerStatefulWidget {
  const JobsScreen({super.key});

  static const _filters = <String?>[
    null,
    'processing',
    'queued',
    'completed',
    'failed',
  ];

  @override
  ConsumerState<JobsScreen> createState() => _JobsScreenState();
}

class _JobsScreenState extends ConsumerState<JobsScreen> {
  final _searchController = TextEditingController();
  String _searchQuery = '';

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final jobs = ref.watch(jobListProvider);
    final filter = ref.watch(jobStatusFilterProvider);

    final allJobs = jobs.valueOrNull?.jobs ?? const <Job>[];
    final totalCount = allJobs.length;
    final processingCount =
        allJobs.where((j) => j.status == 'processing').length;
    final queuedCount = allJobs
        .where((j) => j.status == 'queued' || j.status == 'scheduled')
        .length;
    final completedCount = allJobs.where((j) => j.status == 'completed').length;
    final failedCount = allJobs
        .where((j) => j.status == 'failed' || j.status == 'cancelled')
        .length;

    final query = _searchQuery.trim().toLowerCase();
    final filteredJobs = query.isEmpty
        ? allJobs
        : allJobs.where((j) {
            return j.title.toLowerCase().contains(query) ||
                j.id.toLowerCase().contains(query) ||
                (j.step?.toLowerCase().contains(query) ?? false);
          }).toList();

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
      body: jobs.when(
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
          child: NotificationListener<ScrollNotification>(
            onNotification: (notification) {
              if (notification.metrics.extentAfter < 400) {
                ref.read(jobListProvider.notifier).loadMore();
              }
              return false;
            },
            child: ListView(
              padding: const EdgeInsets.fromLTRB(14, 14, 14, 28),
              children: [
                StudioPageHeader(
                  eyebrow: 'Background Jobs · Thời gian thực',
                  eyebrowPulse: true,
                  title: 'Quản lý tiến trình',
                  subtitle:
                      'Theo dõi trạng thái xử lý video, tải kết quả và đăng bài tự động.',
                  actions: [
                    StudioGradientButton(
                      expanded: false,
                      padding: const EdgeInsets.symmetric(
                        horizontal: 14,
                        vertical: 9,
                      ),
                      icon: Icons.add_rounded,
                      label: 'Batch mới',
                      onPressed: () => context.go('/translate'),
                    ),
                    OutlinedButton.icon(
                      style: OutlinedButton.styleFrom(
                        minimumSize: Size.zero,
                        padding: const EdgeInsets.symmetric(
                          horizontal: 12,
                          vertical: 9,
                        ),
                      ),
                      onPressed: () =>
                          ref.read(jobListProvider.notifier).refresh(),
                      icon: const Icon(Icons.refresh_rounded, size: 16),
                      label: const Text('Làm mới'),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                // 2x2 responsive metric grid matching `.metric-grid` in `web/index.html`
                Row(
                  children: [
                    Expanded(
                      child: StudioMetricCard(
                        label: 'Tổng tiến trình',
                        value: '$totalCount',
                        subtitle: failedCount > 0
                            ? 'Lỗi/Hủy: $failedCount'
                            : 'Trong danh sách',
                        icon: Icons.layers_outlined,
                        accentColor: AppColors.ink,
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: StudioMetricCard(
                        label: 'Đang chạy',
                        value: '$processingCount',
                        subtitle: 'Đang xử lý AI',
                        icon: Icons.autorenew_rounded,
                        accentColor: AppColors.blue,
                        backgroundColor: processingCount > 0
                            ? AppColors.blueTint
                            : AppColors.surface,
                        borderColor: processingCount > 0
                            ? AppColors.blueBorder
                            : AppColors.line,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                Row(
                  children: [
                    Expanded(
                      child: StudioMetricCard(
                        label: 'Đang chờ',
                        value: '$queuedCount',
                        subtitle: 'Trong hàng đợi',
                        icon: Icons.schedule_rounded,
                        accentColor: AppColors.amberDark,
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: StudioMetricCard(
                        label: 'Hoàn tất',
                        value: '$completedCount',
                        subtitle: 'Sẵn sàng tải về',
                        icon: Icons.check_circle_outline_rounded,
                        accentColor: AppColors.greenDark,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                // Toolbar card matching `.jobs-toolbar` on the web
                StudioCard(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      TextField(
                        controller: _searchController,
                        onChanged: (v) => setState(() => _searchQuery = v),
                        decoration: InputDecoration(
                          isDense: true,
                          hintText: 'Tìm theo tên video hoặc mã job...',
                          prefixIcon: const Icon(
                            Icons.search_rounded,
                            size: 19,
                            color: AppColors.muted,
                          ),
                          suffixIcon: _searchQuery.isEmpty
                              ? null
                              : IconButton(
                                  visualDensity: VisualDensity.compact,
                                  icon: const Icon(
                                    Icons.clear_rounded,
                                    size: 17,
                                  ),
                                  onPressed: () {
                                    _searchController.clear();
                                    setState(() => _searchQuery = '');
                                  },
                                ),
                        ),
                      ),
                      const SizedBox(height: 10),
                      SizedBox(
                        height: 36,
                        child: ListView(
                          scrollDirection: Axis.horizontal,
                          children: [
                            for (final status in JobsScreen._filters)
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
                const SizedBox(height: 12),
                if (filteredJobs.isEmpty)
                  StudioEmptyState(
                    icon: Icons.inbox_outlined,
                    title: query.isNotEmpty
                        ? 'Không tìm thấy tiến trình phù hợp'
                        : 'Chưa có công việc nào',
                    subtitle: query.isNotEmpty
                        ? 'Thử từ khóa khác hoặc xóa bộ lọc tìm kiếm.'
                        : 'Chuyển sang tab Dịch video để tải video lên hoặc dán link TikTok/Douyin để tự động xử lý.',
                    action: query.isNotEmpty
                        ? OutlinedButton(
                            onPressed: () {
                              _searchController.clear();
                              setState(() => _searchQuery = '');
                            },
                            child: const Text('Xóa tìm kiếm'),
                          )
                        : StudioGradientButton(
                            expanded: false,
                            icon: Icons.add_circle_outline_rounded,
                            label: 'Tạo công việc mới',
                            onPressed: () => context.go('/translate'),
                          ),
                  )
                else ...[
                  for (var i = 0; i < filteredJobs.length; i++) ...[
                    if (i > 0) const SizedBox(height: 10),
                    _JobTile(job: filteredJobs[i]),
                  ],
                  if (list.hasMore)
                    const Padding(
                      padding: EdgeInsets.all(16),
                      child: Center(
                        child: CircularProgressIndicator(
                          color: AppColors.brand,
                        ),
                      ),
                    ),
                ],
              ],
            ),
          ),
        ),
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
      if (job.queuePosition != null) 'Hàng đợi #${job.queuePosition}',
      if (job.isActive && job.step != null) job.step!,
      if (created != null) DateFormat('dd/MM HH:mm').format(created),
    ].join(' · ');
    final accent = _accentForStatus(job.status);

    return StudioCard(
      leftAccentColor: accent,
      padding: const EdgeInsets.fromLTRB(16, 13, 13, 13),
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
                        fontWeight: FontWeight.w800,
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
