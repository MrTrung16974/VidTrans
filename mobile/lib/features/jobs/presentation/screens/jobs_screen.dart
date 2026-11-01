import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../../home/presentation/screens/home_shell.dart';
import '../../../video_translation/presentation/providers/submission_queue.dart';
import '../../../video_translation/presentation/widgets/pending_submission_card.dart';
import '../../domain/bulk_actions.dart';
import '../../domain/job.dart';
import '../providers/jobs_providers.dart';
import '../widgets/job_bulk_bar.dart';
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

  /// Selection mode (long-press a job, or "Chọn"): taps toggle jobs and the
  /// bottom bar runs cancel / retry / delete on the selected ones.
  bool _selecting = false;
  final _selectedIds = <String>{};
  bool _bulkBusy = false;

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  void _startSelecting([Job? first]) => setState(() {
    _selecting = true;
    if (first != null) _selectedIds.add(first.id);
  });

  void _stopSelecting() => setState(() {
    _selecting = false;
    _selectedIds.clear();
  });

  void _toggle(Job job) => setState(() {
    if (!_selectedIds.remove(job.id)) _selectedIds.add(job.id);
  });

  Future<void> _runBulk(BulkJobAction action, List<Job> selected) async {
    final jobs = action.eligible(selected);
    if (jobs.isEmpty) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('${action.label} ${jobs.length} job?'),
        content: Text(switch (action) {
          BulkJobAction.cancel =>
            'Các job đang chờ hoặc đang xử lý sẽ dừng lại.',
          BulkJobAction.retry => 'Các job sẽ được xếp hàng xử lý lại từ đầu.',
          BulkJobAction.delete =>
            'Job và file kết quả sẽ bị xóa, không khôi phục được.',
        }),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Không'),
          ),
          FilledButton(
            style: action == BulkJobAction.delete
                ? FilledButton.styleFrom(backgroundColor: AppColors.redDark)
                : null,
            onPressed: () => Navigator.pop(context, true),
            child: Text(action.label),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _bulkBusy = true);
    final result = await ref
        .read(jobListProvider.notifier)
        .runBulk(action, jobs);
    if (!mounted) return;
    setState(() {
      _bulkBusy = false;
      _selectedIds.removeAll(result.succeeded);
      if (_selectedIds.isEmpty) _selecting = false;
    });
    messenger.showSnackBar(
      SnackBar(
        content: Text(result.message),
        backgroundColor: result.errors.isEmpty ? null : AppColors.redDark,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final jobs = ref.watch(jobListProvider);
    final filter = ref.watch(jobStatusFilterProvider);
    final pending = ref.watch(submissionQueueProvider);

    final allJobs = jobs.valueOrNull?.jobs ?? const <Job>[];
    final totalCount = allJobs.length;
    final processingCount = allJobs
        .where((j) => j.status == 'processing')
        .length;
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
    final readOnly = ref.watch(sessionProvider).valueOrNull?.isReadOnly ?? true;
    final selectedJobs = allJobs
        .where((job) => _selectedIds.contains(job.id))
        .toList();

    return PopScope(
      // Back leaves selection mode first.
      canPop: !_selecting,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop && !_bulkBusy) _stopSelecting();
      },
      child: Scaffold(
        backgroundColor: AppColors.bg,
        bottomNavigationBar: _selecting
            ? JobBulkBar(
                selected: selectedJobs,
                visibleCount: filteredJobs.length,
                busy: _bulkBusy,
                onSelectAll: () => setState(
                  () => _selectedIds.addAll(filteredJobs.map((job) => job.id)),
                ),
                onClear: () => setState(_selectedIds.clear),
                onClose: _stopSelecting,
                onAction: (action) => _runBulk(action, selectedJobs),
              )
            : null,
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
                      if (!readOnly && allJobs.isNotEmpty)
                        OutlinedButton.icon(
                          style: OutlinedButton.styleFrom(
                            minimumSize: Size.zero,
                            padding: const EdgeInsets.symmetric(
                              horizontal: 12,
                              vertical: 9,
                            ),
                          ),
                          onPressed: _selecting
                              ? _stopSelecting
                              : _startSelecting,
                          icon: Icon(
                            _selecting
                                ? Icons.close_rounded
                                : Icons.checklist_rounded,
                            size: 16,
                          ),
                          label: Text(_selecting ? 'Bỏ chọn' : 'Chọn'),
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
                                    onSelected: (_) =>
                                        ref
                                                .read(
                                                  jobStatusFilterProvider
                                                      .notifier,
                                                )
                                                .state =
                                            status,
                                  ),
                                ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),
                  for (final item in pending) ...[
                    PendingSubmissionCard(item: item),
                    const SizedBox(height: 10),
                  ],
                  if (filteredJobs.isEmpty && pending.isEmpty)
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
                  else if (filteredJobs.isNotEmpty) ...[
                    for (var i = 0; i < filteredJobs.length; i++) ...[
                      if (i > 0) const SizedBox(height: 10),
                      _JobTile(
                        job: filteredJobs[i],
                        selecting: _selecting,
                        selected: _selectedIds.contains(filteredJobs[i].id),
                        onTap: _selecting
                            ? () => _toggle(filteredJobs[i])
                            : () => context.push('/jobs/${filteredJobs[i].id}'),
                        onLongPress: readOnly || _selecting
                            ? null
                            : () => _startSelecting(filteredJobs[i]),
                      ),
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
      ),
    );
  }
}

class _JobTile extends StatelessWidget {
  const _JobTile({
    required this.job,
    required this.onTap,
    this.onLongPress,
    this.selecting = false,
    this.selected = false,
  });

  final Job job;
  final VoidCallback onTap;
  final VoidCallback? onLongPress;
  final bool selecting;
  final bool selected;

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
      backgroundColor: selected ? AppColors.brandTint : AppColors.surface,
      borderColor: selected
          ? AppColors.brand
          : job.status == 'processing'
          ? AppColors.blueBorder
          : AppColors.line,
      onTap: onTap,
      onLongPress: onLongPress,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (selecting) ...[
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Icon(
                    selected
                        ? Icons.check_circle_rounded
                        : Icons.radio_button_unchecked_rounded,
                    size: 24,
                    color: selected ? AppColors.brand : AppColors.muted,
                  ),
                ),
                const SizedBox(width: 10),
              ],
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
