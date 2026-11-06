import 'dart:async';

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
import '../widgets/jobs_pagination.dart';
import '../widgets/job_status_chip.dart';

class JobsScreen extends ConsumerStatefulWidget {
  const JobsScreen({super.key});

  static const _filters = <String?>[
    null,
    'processing',
    'queued',
    'paused',
    'completed',
    'failed',
  ];

  @override
  ConsumerState<JobsScreen> createState() => _JobsScreenState();
}

class _JobsScreenState extends ConsumerState<JobsScreen> {
  final _searchController = TextEditingController();
  final _scrollController = ScrollController();
  Timer? _searchDebounce;
  String _searchQuery = '';

  /// Selection mode (long-press a job, or "Chọn"): taps toggle jobs and the
  /// bottom bar runs cancel / retry / delete on the selected ones.
  bool _selecting = false;
  final _selectedIds = <String>{};
  bool _bulkBusy = false;

  @override
  void dispose() {
    _searchController.dispose();
    _scrollController.dispose();
    _searchDebounce?.cancel();
    super.dispose();
  }

  void _startSelecting([Job? first]) {
    ref.read(isJobSelectingProvider.notifier).state = true;
    setState(() {
      _selecting = true;
      if (first != null) _selectedIds.add(first.id);
    });
  }

  void _stopSelecting() {
    ref.read(isJobSelectingProvider.notifier).state = false;
    setState(() {
      _selecting = false;
      _selectedIds.clear();
    });
  }

  void _toggle(Job job) => setState(() {
    if (!_selectedIds.remove(job.id)) _selectedIds.add(job.id);
  });

  /// Search runs on the server, like the web, so it covers every page.
  void _onSearch(String value) {
    setState(() => _searchQuery = value);
    _searchDebounce?.cancel();
    _searchDebounce = Timer(const Duration(milliseconds: 300), () {
      ref.read(jobPageProvider.notifier).state = 1;
      ref.read(jobSearchProvider.notifier).state = value.trim();
    });
  }

  void _clearSearch() {
    _searchController.clear();
    _searchDebounce?.cancel();
    setState(() => _searchQuery = '');
    ref.read(jobPageProvider.notifier).state = 1;
    ref.read(jobSearchProvider.notifier).state = '';
  }

  void _goToPage(int page) {
    ref.read(jobPageProvider.notifier).state = page;
    if (_scrollController.hasClients) {
      _scrollController.animateTo(
        0,
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
      );
    }
  }

  void _setPageSize(int size) {
    ref.read(jobPageProvider.notifier).state = 1;
    ref.read(jobPageSizeProvider.notifier).state = size;
  }

  Future<void> _runBulk(BulkJobAction action, List<Job> selected) async {
    final jobs = action.eligible(selected);
    if (jobs.isEmpty) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('${action.label} ${jobs.length} job?'),
        content: Text(switch (action) {
          BulkJobAction.pause =>
            'Các job sẽ dừng ở điểm an toàn và giữ nguyên tiến độ.',
          BulkJobAction.resume => 'Các job đã tạm dừng sẽ trở lại hàng đợi.',
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

    final list = jobs.valueOrNull;
    final allJobs = list?.jobs ?? const <Job>[];
    // Counts span every page (server `counts`), as on the web dashboard.
    final totalCount = list?.allCount ?? 0;
    final processingCount =
        list?.countOf(const ['processing', 'pausing', 'cancelling']) ?? 0;
    final queuedCount =
        list?.countOf(const ['queued', 'scheduled', 'paused']) ?? 0;
    final completedCount = list?.countOf(const ['completed']) ?? 0;
    final failedCount = list?.countOf(const ['failed', 'cancelled']) ?? 0;

    final query = _searchQuery.trim();
    final filteredJobs = allJobs;
    final readOnly = ref.watch(sessionProvider).valueOrNull?.isReadOnly ?? true;
    final canCutClips =
        ref.watch(sessionProvider).valueOrNull?.canCutClips ?? false;
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
            child: ListView(
              controller: _scrollController,
              padding: EdgeInsets.fromLTRB(
                14,
                14,
                14,
                studioDockBottomPadding(context),
              ),
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
                    if (!readOnly && canCutClips)
                      OutlinedButton.icon(
                        style: OutlinedButton.styleFrom(
                          minimumSize: Size.zero,
                          padding: const EdgeInsets.symmetric(
                            horizontal: 12,
                            vertical: 9,
                          ),
                        ),
                        onPressed: () => context.push('/clips'),
                        icon: const Icon(Icons.content_cut_rounded, size: 16),
                        label: const Text('Cắt clip'),
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
                        selected: filter == null,
                        onTap: () {
                          ref.read(jobPageProvider.notifier).state = 1;
                          ref.read(jobStatusFilterProvider.notifier).state =
                              null;
                        },
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
                        selected: filter == 'processing',
                        onTap: () {
                          ref.read(jobPageProvider.notifier).state = 1;
                          ref.read(jobStatusFilterProvider.notifier).state =
                              filter == 'processing' ? null : 'processing';
                        },
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
                        selected: filter == 'queued',
                        onTap: () {
                          ref.read(jobPageProvider.notifier).state = 1;
                          ref.read(jobStatusFilterProvider.notifier).state =
                              filter == 'queued' ? null : 'queued';
                        },
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
                        selected: filter == 'completed',
                        onTap: () {
                          ref.read(jobPageProvider.notifier).state = 1;
                          ref.read(jobStatusFilterProvider.notifier).state =
                              filter == 'completed' ? null : 'completed';
                        },
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
                        onChanged: _onSearch,
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
                                  onPressed: _clearSearch,
                                ),
                        ),
                      ),
                      const SizedBox(height: 10),
                      SizedBox(
                        height: 36,
                        child: ShaderMask(
                          shaderCallback: (bounds) {
                            return const LinearGradient(
                              begin: Alignment.centerLeft,
                              end: Alignment.centerRight,
                              colors: [
                                Colors.black,
                                Colors.black,
                                Colors.transparent,
                              ],
                              stops: [0.0, 0.92, 1.0],
                            ).createShader(bounds);
                          },
                          blendMode: BlendMode.dstIn,
                          child: ListView(
                            scrollDirection: Axis.horizontal,
                            padding: const EdgeInsets.only(right: 16),
                            physics: const BouncingScrollPhysics(),
                            children: [
                              for (final status in JobsScreen._filters)
                                Padding(
                                  padding: const EdgeInsets.only(right: 6),
                                  child: ChoiceChip(
                                    showCheckmark: false,
                                    visualDensity: VisualDensity.compact,
                                    padding: const EdgeInsets.symmetric(
                                      horizontal: 8,
                                      vertical: 2,
                                    ),
                                    label: Text(
                                      status == null
                                          ? 'Tất cả'
                                          : status == 'paused'
                                              ? 'Tạm dừng'
                                              : status == 'completed'
                                                  ? 'Hoàn tất'
                                                  : jobStatusLabels[status]!,
                                      style: TextStyle(
                                        fontSize: 12,
                                        fontWeight: filter == status
                                            ? FontWeight.w800
                                            : FontWeight.w600,
                                      ),
                                    ),
                                    selected: filter == status,
                                    onSelected: (_) {
                                      ref.read(jobPageProvider.notifier).state =
                                          1;
                                      ref
                                              .read(
                                                jobStatusFilterProvider.notifier,
                                              )
                                              .state =
                                          status;
                                    },
                                  ),
                                ),
                            ],
                          ),
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
                            onPressed: _clearSearch,
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
                  const SizedBox(height: 14),
                  JobsPagination(
                    list: list,
                    onPage: _goToPage,
                    onPageSize: _setPageSize,
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
      'queued' ||
      'pausing' ||
      'cancelling' ||
      'awaiting_input' => AppColors.amber,
      'paused' => AppColors.muted,
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
      if (job.isActive && job.stepLabel != null) job.stepLabel!,
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
