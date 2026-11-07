import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:video_player/video_player.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../../video_translation/data/translation_repository.dart';
import '../../../video_translation/domain/options.dart';
import '../../data/jobs_repository.dart';
import '../../domain/job.dart';
import '../providers/jobs_providers.dart';
import '../widgets/job_downloads.dart';
import '../widgets/job_status_chip.dart';

class JobDetailScreen extends ConsumerStatefulWidget {
  const JobDetailScreen({
    super.key,
    required this.jobId,
    this.askDownloadOnly = false,
  });

  final String jobId;

  /// Opened from the "Video không có lời" push: ask once whether to just download it.
  final bool askDownloadOnly;

  @override
  ConsumerState<JobDetailScreen> createState() => _JobDetailScreenState();
}

class _JobDetailScreenState extends ConsumerState<JobDetailScreen> {
  bool _asked = false;

  String get jobId => widget.jobId;

  void _maybeAskDownloadOnly(Job job) {
    if (_asked || !widget.askDownloadOnly || !job.canDownloadOnly) return;
    _asked = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) confirmDownloadOnly(context, ref, job);
    });
  }

  @override
  Widget build(BuildContext context) {
    final job = ref.watch(jobDetailProvider(jobId));
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: StudioSubBar(
        title: 'Chi tiết tiến trình',
        subtitle:
            'Job #${jobId.substring(0, jobId.length.clamp(0, 8)).toUpperCase()}',
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh_rounded, size: 20),
            tooltip: 'Làm mới',
            onPressed: () => ref.invalidate(jobDetailProvider(jobId)),
          ),
        ],
      ),
      body: job.when(
        loading: () => const Center(
          child: CircularProgressIndicator(color: AppColors.brand),
        ),
        error: (error, _) => ErrorView(
          message: errorMessage(error),
          onRetry: () => ref.invalidate(jobDetailProvider(jobId)),
        ),
        data: (job) {
          _maybeAskDownloadOnly(job);
          return _JobDetail(job: job);
        },
      ),
    );
  }
}

/// Asks whether to re-run a voiceless job as "Chỉ tải video" and does it on yes.
Future<void> confirmDownloadOnly(
  BuildContext context,
  WidgetRef ref,
  Job job,
) async {
  final confirmed = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('Video không có lời để dịch'),
      content: Text(
        '"${job.title}" chỉ có nhạc hoặc tiếng hát không rõ lời, nên không tạo được '
        'phụ đề hay lồng tiếng.\n\nBạn có muốn chỉ tải video gốc về không? Video giữ '
        'nguyên hình và tiếng, không phụ đề, không lồng tiếng.',
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('Không'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(true),
          child: const Text('Chỉ tải video'),
        ),
      ],
    ),
  );
  if (confirmed != true || !context.mounted) return;
  final messenger = ScaffoldMessenger.of(context);
  try {
    await ref.read(jobsRepositoryProvider).downloadOnly(job.id);
    messenger.showSnackBar(
      const SnackBar(content: Text('Đang chuẩn bị video gốc để tải về')),
    );
    ref.invalidate(jobDetailProvider(job.id));
  } catch (error) {
    messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
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
    final account = ref.watch(sessionProvider).valueOrNull;
    final readOnly = account?.isReadOnly ?? true;
    final canPublish = account?.canPublishSocial ?? false;

    return ListView(
      padding: EdgeInsets.fromLTRB(
        16,
        16,
        16,
        studioDockBottomPadding(context, extra: 44),
      ),
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
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        job.stepLabel ?? 'Đang cập nhật',
                        style: const TextStyle(
                          fontSize: 13.5,
                          fontWeight: FontWeight.w800,
                          color: AppColors.ink,
                        ),
                      ),
                    ),
                    if (job.progress != null)
                      Text(
                        '${(job.progress! * 100).round()}%',
                        style: const TextStyle(
                          fontSize: 13.5,
                          fontWeight: FontWeight.w900,
                          color: AppColors.brand,
                        ),
                      ),
                  ],
                ),
                const SizedBox(height: 8),
                ClipRRect(
                  borderRadius: BorderRadius.circular(999),
                  child: LinearProgressIndicator(
                    value: job.progress,
                    minHeight: 8,
                  ),
                ),
                if (job.stepDetail != null) ...[
                  const SizedBox(height: 8),
                  Text(
                    job.stepDetail!,
                    style: const TextStyle(
                      fontSize: 12.5,
                      fontWeight: FontWeight.w600,
                      color: AppColors.inkSoft,
                    ),
                  ),
                ],
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
                if (job.status == 'completed' && !readOnly && canPublish) ...[
                  const SizedBox(height: 10),
                  OutlinedButton.icon(
                    style: OutlinedButton.styleFrom(
                      foregroundColor: Colors.white,
                      iconColor: Colors.white,
                      backgroundColor: const Color(0x14FFFFFF),
                      side: const BorderSide(color: Color(0xFF475569)),
                    ),
                    onPressed: () => context.push('/social/publish/${job.id}'),
                    icon: const Icon(Icons.share_rounded, size: 18),
                    label: const Text('Đăng mạng xã hội'),
                  ),
                ],
                if (job.status == 'completed' &&
                    !readOnly &&
                    (account?.canCutClips ?? false)) ...[
                  const SizedBox(height: 10),
                  OutlinedButton.icon(
                    style: OutlinedButton.styleFrom(
                      foregroundColor: Colors.white,
                      iconColor: Colors.white,
                      backgroundColor: const Color(0x14FFFFFF),
                      side: const BorderSide(color: Color(0xFF475569)),
                    ),
                    // Opens the separate "Cắt clip" feature on this video.
                    onPressed: () => context.push('/clips?source=${job.id}'),
                    icon: const Icon(Icons.content_cut_rounded, size: 18),
                    label: const Text('Cắt thành nhiều clip'),
                  ),
                ],
              ],
            ),
          ),
        ],
        if (job.awaitsPhotoChoice &&
            !readOnly &&
            (account?.canUsePhotoPosts ?? false))
          _PhotoPostChoiceCard(job: job),
        if (!readOnly && (job.canPause || job.canResume || job.canCancel || job.canDownloadOnly || job.canRetry)) ...[
          const SizedBox(height: 12),
          StudioCard(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
            child: Wrap(
              spacing: 8,
              runSpacing: 8,
              alignment: WrapAlignment.center,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                if (job.canPause)
                  FilledButton.tonalIcon(
                    onPressed: () => _run(
                      context,
                      ref,
                      () => repo.pause(job.id),
                      'Đang tạm dừng tại điểm an toàn',
                    ),
                    icon: const Icon(Icons.pause_rounded, size: 18),
                    label: const Text('Tạm dừng'),
                  ),
                if (job.canResume)
                  FilledButton.icon(
                    onPressed: () => _run(
                      context,
                      ref,
                      () => repo.resume(job.id),
                      'Đã đưa công việc trở lại hàng đợi',
                    ),
                    icon: const Icon(Icons.play_arrow_rounded, size: 18),
                    label: const Text('Tiếp tục'),
                  ),
                if (job.canRetry)
                  FilledButton.tonalIcon(
                    onPressed: () => _run(
                      context,
                      ref,
                      () => repo.retry(job.id),
                      'Đã đưa vào hàng đợi lại',
                    ),
                    icon: const Icon(Icons.refresh_rounded, size: 18),
                    label: const Text('Chạy lại'),
                  ),
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
                      size: 18,
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
                if (job.canDownloadOnly)
                  FilledButton.icon(
                    onPressed: () => confirmDownloadOnly(context, ref, job),
                    icon: const Icon(Icons.download_rounded, size: 18),
                    label: const Text('Chỉ tải video (không lồng tiếng)'),
                  ),
              ],
            ),
          ),
        ],
        JobDownloads(job: job),
        const SizedBox(height: 14),
        _JobInfoCard(job: job),
        const SizedBox(height: 14),
        _JobTimelineCard(job: job),
        if (!readOnly) ...[
          const SizedBox(height: 16),
          if (job.canPause)
            FilledButton.tonalIcon(
              onPressed: () => _run(
                context,
                ref,
                () => repo.pause(job.id),
                'Đang tạm dừng tại điểm an toàn',
              ),
              icon: const Icon(Icons.pause_rounded),
              label: const Text('Tạm dừng'),
            ),
          if (job.canResume) ...[
            FilledButton.icon(
              onPressed: () => _run(
                context,
                ref,
                () => repo.resume(job.id),
                'Đã đưa công việc trở lại hàng đợi',
              ),
              icon: const Icon(Icons.play_arrow_rounded),
              label: const Text('Tiếp tục'),
            ),
          ],
          if (job.canCancel) ...[
            if (job.canPause || job.canResume) const SizedBox(height: 10),
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
          ],
          if (job.canDownloadOnly) ...[
            const SizedBox(height: 10),
            FilledButton.icon(
              onPressed: () => confirmDownloadOnly(context, ref, job),
              icon: const Icon(Icons.download_rounded),
              label: const Text('Chỉ tải video (không lồng tiếng)'),
            ),
          ],
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

/// Lengths offered for a photo-post slideshow, in minutes.
const _slideshowMinutes = [0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0];

String _minutesLabel(double minutes) => minutes < 1
    ? '${(minutes * 60).round()} giây'
    : '${minutes.toStringAsFixed(0)} phút';

/// At most this many library tracks per slideshow (the server's limit).
const _maxMusicTracks = 10;

String _trackLabel(List<MusicTrack> tracks, String trackId) {
  for (final track in tracks) {
    if (track.id == trackId) return '${track.title} (${track.durationLabel})';
  }
  return trackId;
}

/// A photo post waits here: download its images, or a slideshow of a chosen
/// length with the post's music or a library track.
class _PhotoPostChoiceCard extends ConsumerStatefulWidget {
  const _PhotoPostChoiceCard({required this.job});

  final Job job;

  @override
  ConsumerState<_PhotoPostChoiceCard> createState() =>
      _PhotoPostChoiceCardState();
}

class _PhotoPostChoiceCardState extends ConsumerState<_PhotoPostChoiceCard> {
  double _minutes = 1;

  /// Library tracks in play order; empty keeps the post's own music.
  final List<String> _musicTrackIds = [];
  VideoPlayerController? _musicPreviewPlayer;
  String? _musicPreviewTrackId;
  String? _musicPreviewLoadingTrackId;
  bool _sending = false;
  bool _suggesting = false;
  MusicSuggestions? _suggestions;
  bool _planning = false;
  bool _keepOriginalOrder = false;
  PhotoSlideshowPlan? _aiPlan;
  PhotoSlideshowPlan? _appliedAiPlan;

  @override
  void didUpdateWidget(_PhotoPostChoiceCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.job.id != widget.job.id) {
      _stopMusicPreview();
      _musicTrackIds.clear();
      _aiPlan = null;
      _appliedAiPlan = null;
    }
  }

  @override
  void dispose() {
    _musicPreviewPlayer?.dispose();
    super.dispose();
  }

  Future<void> _stopMusicPreview() async {
    final player = _musicPreviewPlayer;
    _musicPreviewPlayer = null;
    _musicPreviewTrackId = null;
    _musicPreviewLoadingTrackId = null;
    await player?.dispose();
    if (mounted) setState(() {});
  }

  Future<void> _toggleMusicPreview(String trackId) async {
    if (_musicPreviewLoadingTrackId != null) return;
    if (_musicPreviewTrackId == trackId) {
      await _stopMusicPreview();
      return;
    }

    await _stopMusicPreview();
    if (!mounted) return;
    setState(() => _musicPreviewLoadingTrackId = trackId);
    final repo = ref.read(translationRepositoryProvider);
    VideoPlayerController? player;
    try {
      player = VideoPlayerController.networkUrl(
        repo.musicTrackAudioUri(trackId),
        httpHeaders: await repo.authHeaders(),
      );
      await player.initialize();
      if (!mounted || _musicPreviewLoadingTrackId != trackId) {
        await player.dispose();
        return;
      }
      player.addListener(() {
        final value = player!.value;
        if (value.isInitialized &&
            !value.isPlaying &&
            value.position >= value.duration &&
            _musicPreviewPlayer == player) {
          _stopMusicPreview();
        }
      });
      await player.play();
      if (!mounted) {
        await player.dispose();
        return;
      }
      setState(() {
        _musicPreviewPlayer = player;
        _musicPreviewTrackId = trackId;
        _musicPreviewLoadingTrackId = null;
      });
    } catch (_) {
      await player?.dispose();
      if (!mounted) return;
      setState(() => _musicPreviewLoadingTrackId = null);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Không phát được bản nhạc.')),
      );
    }
  }

  Future<void> _removeTrack(int index) async {
    final trackId = _musicTrackIds[index];
    if (_musicPreviewTrackId == trackId ||
        _musicPreviewLoadingTrackId == trackId) {
      await _stopMusicPreview();
    }
    if (mounted) setState(() => _musicTrackIds.removeAt(index));
  }

  Future<void> _suggestMusic() async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _suggesting = true);
    try {
      final suggestions = await ref
          .read(jobsRepositoryProvider)
          .photoPostMusicSuggestions(widget.job.id);
      if (!mounted) return;
      setState(() => _suggestions = suggestions);
      if (suggestions.items.isEmpty) {
        messenger.showSnackBar(
          const SnackBar(
            content: Text('AI chưa tìm được bài nào hợp trong thư viện nhạc'),
          ),
        );
      }
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    } finally {
      if (mounted) setState(() => _suggesting = false);
    }
  }

  Future<void> _planSlideshow() async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _planning = true);
    try {
      final plan = await ref
          .read(jobsRepositoryProvider)
          .photoPostAiPlan(
            widget.job.id,
            targetMinutes: _minutes,
            keepOriginalOrder: _keepOriginalOrder,
          );
      if (!mounted) return;
      setState(() {
        _aiPlan = plan;
        _appliedAiPlan = null;
        _suggestions = MusicSuggestions(
          items: plan.musicSuggestions,
          mood: plan.mood,
          byAi: plan.byAi,
        );
      });
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    } finally {
      if (mounted) setState(() => _planning = false);
    }
  }

  void _applyAiPlan() {
    final plan = _aiPlan;
    if (plan == null) return;
    setState(() {
      _appliedAiPlan = plan;
      _minutes = plan.targetSeconds / 60;
      final firstTrack = plan.musicSuggestions.isEmpty
          ? null
          : plan.musicSuggestions.first.trackId;
      if (firstTrack != null &&
          !_musicTrackIds.contains(firstTrack) &&
          _musicTrackIds.length < _maxMusicTracks) {
        _musicTrackIds.add(firstTrack);
      }
    });
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Đã áp dụng phương án dựng AI')),
    );
  }

  void _addTracks(Iterable<String> trackIds) {
    setState(() {
      for (final trackId in trackIds) {
        if (_musicTrackIds.length >= _maxMusicTracks) break;
        if (!_musicTrackIds.contains(trackId)) _musicTrackIds.add(trackId);
      }
    });
  }

  Widget _aiPlanTag(String label, {bool highlight = false}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
        color: highlight ? const Color(0xFFF5F3FF) : Colors.white,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(
          color: highlight ? const Color(0xFFC4B5FD) : AppColors.line,
        ),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w800,
          color: highlight ? const Color(0xFF6D28D9) : AppColors.inkSecondary,
        ),
      ),
    );
  }

  Widget _suggestionList(MusicSuggestions suggestions) {
    final pending = [
      for (final item in suggestions.items)
        if (!_musicTrackIds.contains(item.trackId)) item.trackId,
    ];
    final full = _musicTrackIds.length >= _maxMusicTracks;
    return Container(
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.amberTint,
        borderRadius: BorderRadius.circular(13),
        border: Border.all(color: AppColors.amberBorder),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Icon(
                Icons.auto_awesome,
                size: 16,
                color: AppColors.amberDark,
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  suggestions.byAi
                      ? 'AI gợi ý${suggestions.mood.isEmpty ? '' : ' · ${suggestions.mood}'}'
                      : 'Gợi ý theo từ khoá caption (AI đang không khả dụng)',
                  style: const TextStyle(
                    fontWeight: FontWeight.w800,
                    fontSize: 12.5,
                    color: AppColors.amberDark,
                  ),
                ),
              ),
              if (pending.length > 1)
                TextButton(
                  style: TextButton.styleFrom(
                    visualDensity: VisualDensity.compact,
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                  ),
                  onPressed: _sending || full
                      ? null
                      : () => _addTracks(pending),
                  child: const Text(
                    'Thêm tất cả',
                    style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
                  ),
                ),
            ],
          ),
          const SizedBox(height: 6),
          for (final item in suggestions.items)
            Container(
              margin: const EdgeInsets.only(top: 6),
              padding: const EdgeInsets.fromLTRB(10, 8, 4, 8),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: AppColors.line),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          item.title,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 12.8,
                            fontWeight: FontWeight.w700,
                            color: AppColors.ink,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          item.reason.isEmpty ? item.category : item.reason,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 11.2,
                            color: AppColors.muted,
                            height: 1.3,
                          ),
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    visualDensity: VisualDensity.compact,
                    tooltip: _musicPreviewTrackId == item.trackId
                        ? 'Dừng'
                        : 'Nghe thử',
                    icon: _musicPreviewLoadingTrackId == item.trackId
                        ? const SizedBox.square(
                            dimension: 17,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : Icon(
                            _musicPreviewTrackId == item.trackId
                                ? Icons.stop_circle_outlined
                                : Icons.play_circle_outline,
                            color: AppColors.brand,
                            size: 21,
                          ),
                    onPressed: _musicPreviewLoadingTrackId != null
                        ? null
                        : () => _toggleMusicPreview(item.trackId),
                  ),
                  if (_musicTrackIds.contains(item.trackId))
                    const Padding(
                      padding: EdgeInsets.symmetric(horizontal: 10),
                      child: Icon(
                        Icons.check_circle_rounded,
                        size: 20,
                        color: AppColors.green,
                      ),
                    )
                  else
                    IconButton(
                      visualDensity: VisualDensity.compact,
                      tooltip: 'Thêm bài này',
                      icon: const Icon(
                        Icons.add_circle_outline_rounded,
                        color: AppColors.amberDark,
                        size: 21,
                      ),
                      onPressed: _sending || full
                          ? null
                          : () => _addTracks([item.trackId]),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Future<void> _choose(String action) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _sending = true);
    try {
      await ref
          .read(jobsRepositoryProvider)
          .choosePhotoPost(
            widget.job.id,
            action: action,
            minutes: action == 'video' ? _minutes : null,
            musicTrackIds: action == 'video' ? List.of(_musicTrackIds) : null,
            slideshowPlan: action == 'video' ? _appliedAiPlan?.toJson() : null,
          );
      messenger.showSnackBar(
        SnackBar(
          content: Text(
            action == 'images'
                ? 'Đang gom ảnh để tải về'
                : 'Đang dựng video ghép ảnh',
          ),
        ),
      );
      ref.invalidate(jobDetailProvider(widget.job.id));
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final job = widget.job;
    final tracks =
        ref.watch(musicTracksProvider).valueOrNull ?? const <MusicTrack>[];
    final durationOptions = <double>{
      ..._slideshowMinutes,
      if (_aiPlan != null) _aiPlan!.targetSeconds / 60,
    }.toList()..sort();
    return Padding(
      padding: const EdgeInsets.only(top: 14),
      child: StudioCard(
        topAccentColor: AppColors.brand,
        borderColor: AppColors.brandBorder,
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Header: icon, title & badge
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 42,
                  height: 42,
                  decoration: BoxDecoration(
                    color: AppColors.brandTint,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(
                      color: AppColors.brandBorder,
                      width: 1.5,
                    ),
                  ),
                  child: const Icon(
                    Icons.photo_library_rounded,
                    color: AppColors.brand,
                    size: 22,
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Text(
                            'Bài đăng ảnh',
                            style: Theme.of(context).textTheme.titleMedium
                                ?.copyWith(
                                  fontWeight: FontWeight.w800,
                                  color: AppColors.ink,
                                  letterSpacing: -0.2,
                                ),
                          ),
                          const SizedBox(width: 8),
                          Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 8,
                              vertical: 3,
                            ),
                            decoration: BoxDecoration(
                              color: AppColors.brandTint,
                              borderRadius: BorderRadius.circular(999),
                              border: Border.all(color: AppColors.brandBorder),
                            ),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Container(
                                  width: 6,
                                  height: 6,
                                  decoration: const BoxDecoration(
                                    color: AppColors.brand,
                                    shape: BoxShape.circle,
                                  ),
                                ),
                                const SizedBox(width: 5),
                                const Text(
                                  'Chờ bạn chọn',
                                  style: TextStyle(
                                    fontSize: 11,
                                    fontWeight: FontWeight.w700,
                                    color: AppColors.brandDark,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 3),
                      Text(
                        'Link này là bài đăng ${job.photoImageCount} ảnh, không phải video. Chọn cách xử lý:',
                        style: const TextStyle(
                          fontSize: 13,
                          color: AppColors.muted,
                          height: 1.35,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),

            // Option 1: Chỉ tải ảnh về (ZIP)
            Container(
              decoration: BoxDecoration(
                color: AppColors.surfaceSoft,
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: AppColors.line),
              ),
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Container(
                        width: 36,
                        height: 36,
                        decoration: BoxDecoration(
                          color: AppColors.surfaceMuted,
                          borderRadius: BorderRadius.circular(10),
                          border: Border.all(color: AppColors.line),
                        ),
                        child: const Icon(
                          Icons.folder_zip_outlined,
                          size: 20,
                          color: AppColors.inkSecondary,
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                const Text(
                                  'Chỉ tải ảnh về',
                                  style: TextStyle(
                                    fontSize: 14,
                                    fontWeight: FontWeight.w800,
                                    color: AppColors.ink,
                                  ),
                                ),
                                const SizedBox(width: 6),
                                Container(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 6,
                                    vertical: 2,
                                  ),
                                  decoration: BoxDecoration(
                                    color: AppColors.surfaceMuted,
                                    borderRadius: BorderRadius.circular(5),
                                    border: Border.all(color: AppColors.line),
                                  ),
                                  child: const Text(
                                    'Tệp ZIP',
                                    style: TextStyle(
                                      fontSize: 10,
                                      fontWeight: FontWeight.w700,
                                      color: AppColors.muted,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 2),
                            const Text(
                              'Nhận toàn bộ ảnh gốc chất lượng cao trong một file ZIP.',
                              style: TextStyle(
                                fontSize: 12,
                                color: AppColors.muted,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  OutlinedButton.icon(
                    onPressed: _sending ? null : () => _choose('images'),
                    style: OutlinedButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 10),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(10),
                      ),
                      side: const BorderSide(color: AppColors.lineStrong),
                    ),
                    icon: const Icon(Icons.download_rounded, size: 18),
                    label: const Text(
                      'Tải ảnh về (ZIP)',
                      style: TextStyle(fontWeight: FontWeight.w700),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),

            // Option 2: Tạo video ghép ảnh có nhạc (Featured)
            Container(
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: AppColors.brandBorder, width: 1.5),
                boxShadow: [
                  BoxShadow(
                    color: AppColors.brand.withValues(alpha: 0.06),
                    blurRadius: 16,
                    offset: const Offset(0, 4),
                  ),
                ],
              ),
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Container(
                        width: 36,
                        height: 36,
                        decoration: BoxDecoration(
                          color: AppColors.brandTint,
                          borderRadius: BorderRadius.circular(10),
                          border: Border.all(color: AppColors.brandBorder),
                        ),
                        child: const Icon(
                          Icons.movie_creation_outlined,
                          size: 20,
                          color: AppColors.brand,
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                const Flexible(
                                  child: Text(
                                    'Tạo video ghép ảnh',
                                    overflow: TextOverflow.ellipsis,
                                    style: TextStyle(
                                      fontSize: 14.5,
                                      fontWeight: FontWeight.w800,
                                      color: AppColors.ink,
                                    ),
                                  ),
                                ),
                                const SizedBox(width: 6),
                                Container(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 7,
                                    vertical: 2,
                                  ),
                                  decoration: BoxDecoration(
                                    color: AppColors.brandTint,
                                    borderRadius: BorderRadius.circular(6),
                                    border: Border.all(
                                      color: AppColors.brandBorder,
                                    ),
                                  ),
                                  child: const Text(
                                    '✨ Khuyên dùng',
                                    style: TextStyle(
                                      fontSize: 10.5,
                                      fontWeight: FontWeight.w800,
                                      color: AppColors.brandDark,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 3),
                            const Text(
                              'Mỗi ảnh hiện tối đa 8 giây, có zoom nhẹ (Ken Burns); video dài thì bộ ảnh được lặp lại cho đủ thời lượng.',
                              style: TextStyle(
                                fontSize: 12,
                                color: AppColors.muted,
                                height: 1.35,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  Container(
                    padding: const EdgeInsets.all(14),
                    decoration: BoxDecoration(
                      gradient: const LinearGradient(
                        colors: [Color(0xFFFAF5FF), Color(0xFFFFF7ED)],
                      ),
                      borderRadius: BorderRadius.circular(14),
                      border: Border.all(
                        color: const Color(0xFFD8B4FE),
                        width: 1.4,
                      ),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    '✨ AI dựng tối ưu',
                                    style: TextStyle(
                                      fontSize: 13.5,
                                      fontWeight: FontWeight.w800,
                                      color: Color(0xFF4C1D95),
                                    ),
                                  ),
                                  SizedBox(height: 3),
                                  Text(
                                    'Phân tích ảnh và caption để chọn nhịp dựng, chuyển động, chuyển cảnh và nhạc.',
                                    style: TextStyle(
                                      fontSize: 11.5,
                                      color: AppColors.muted,
                                      height: 1.38,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            const SizedBox(width: 10),
                            OutlinedButton.icon(
                              style: OutlinedButton.styleFrom(
                                foregroundColor: const Color(0xFF6D28D9),
                                backgroundColor: Colors.white,
                                side: const BorderSide(
                                  color: Color(0xFFC4B5FD),
                                  width: 1.3,
                                ),
                                shape: RoundedRectangleBorder(
                                  borderRadius: BorderRadius.circular(10),
                                ),
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 11,
                                  vertical: 8,
                                ),
                                visualDensity: VisualDensity.compact,
                              ),
                              onPressed: _sending || _planning
                                  ? null
                                  : _planSlideshow,
                              icon: _planning
                                  ? const SizedBox.square(
                                      dimension: 14,
                                      child: CircularProgressIndicator(
                                        strokeWidth: 2,
                                        color: Color(0xFF6D28D9),
                                      ),
                                    )
                                  : const Icon(Icons.auto_awesome, size: 16),
                              label: Text(
                                _planning
                                    ? 'Đang phân tích'
                                    : _aiPlan == null
                                    ? 'Phân tích AI'
                                    : 'Phân tích lại',
                                style: const TextStyle(
                                  fontSize: 12,
                                  fontWeight: FontWeight.w800,
                                ),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 8),
                        InkWell(
                          borderRadius: BorderRadius.circular(9),
                          onTap: _planning
                              ? null
                              : () => setState(
                                  () =>
                                      _keepOriginalOrder = !_keepOriginalOrder,
                                ),
                          child: Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 10,
                              vertical: 6,
                            ),
                            decoration: BoxDecoration(
                              color: Colors.white.withValues(alpha: 0.72),
                              borderRadius: BorderRadius.circular(9),
                              border: Border.all(
                                color: const Color(0xFFDDD6FE),
                              ),
                            ),
                            child: Row(
                              children: [
                                SizedBox(
                                  width: 20,
                                  height: 20,
                                  child: Checkbox(
                                    value: _keepOriginalOrder,
                                    activeColor: const Color(0xFF7C3AED),
                                    shape: RoundedRectangleBorder(
                                      borderRadius: BorderRadius.circular(4),
                                    ),
                                    onChanged: _planning
                                        ? null
                                        : (value) => setState(
                                            () => _keepOriginalOrder =
                                                value ?? false,
                                          ),
                                  ),
                                ),
                                const SizedBox(width: 8),
                                const Expanded(
                                  child: Text(
                                    'Giữ nguyên thứ tự ảnh gốc',
                                    style: TextStyle(
                                      fontSize: 12,
                                      fontWeight: FontWeight.w700,
                                      color: AppColors.inkSecondary,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                        if (_aiPlan case final plan?) ...[
                          const Padding(
                            padding: EdgeInsets.symmetric(vertical: 10),
                            child: Divider(height: 1, color: Color(0xFFDDD6FE)),
                          ),
                          Wrap(
                            spacing: 6,
                            runSpacing: 6,
                            children: [
                              _aiPlanTag(plan.style, highlight: true),
                              if (plan.mood.isNotEmpty) _aiPlanTag(plan.mood),
                              _aiPlanTag('${plan.slidesCount} cảnh'),
                              _aiPlanTag(
                                plan.byAi
                                    ? 'AI phân tích'
                                    : 'Phương án dự phòng',
                              ),
                            ],
                          ),
                          if (plan.summary.isNotEmpty) ...[
                            const SizedBox(height: 7),
                            Text(
                              plan.summary,
                              style: const TextStyle(
                                fontSize: 12,
                                fontWeight: FontWeight.w600,
                                color: AppColors.ink,
                                height: 1.4,
                              ),
                            ),
                          ],
                          const SizedBox(height: 10),
                          Align(
                            alignment: Alignment.centerLeft,
                            child: FilledButton.icon(
                              style: FilledButton.styleFrom(
                                backgroundColor: const Color(0xFF6D28D9),
                                foregroundColor: Colors.white,
                                shape: RoundedRectangleBorder(
                                  borderRadius: BorderRadius.circular(10),
                                ),
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 14,
                                  vertical: 8,
                                ),
                                visualDensity: VisualDensity.compact,
                              ),
                              onPressed: _appliedAiPlan == plan
                                  ? null
                                  : _applyAiPlan,
                              icon: Icon(
                                _appliedAiPlan == plan
                                    ? Icons.check_rounded
                                    : Icons.auto_fix_high_rounded,
                                size: 17,
                              ),
                              label: Text(
                                _appliedAiPlan == plan
                                    ? 'Đã áp dụng'
                                    : 'Áp dụng phương án',
                                style: const TextStyle(
                                  fontSize: 12.5,
                                  fontWeight: FontWeight.w800,
                                ),
                              ),
                            ),
                          ),
                        ],
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),
                  DropdownButtonFormField<double>(
                    initialValue: _minutes,
                    decoration: InputDecoration(
                      labelText: 'Độ dài video',
                      prefixIcon: const Icon(
                        Icons.schedule_rounded,
                        size: 20,
                        color: AppColors.brand,
                      ),
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: 14,
                        vertical: 10,
                      ),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(11),
                        borderSide: const BorderSide(color: AppColors.line),
                      ),
                      enabledBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(11),
                        borderSide: const BorderSide(color: AppColors.line),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(11),
                        borderSide: const BorderSide(
                          color: AppColors.brand,
                          width: 1.5,
                        ),
                      ),
                    ),
                    items: [
                      for (final minutes in durationOptions)
                        DropdownMenuItem(
                          value: minutes,
                          child: Text(
                            _minutesLabel(minutes),
                            style: const TextStyle(fontWeight: FontWeight.w600),
                          ),
                        ),
                    ],
                    onChanged: _sending
                        ? null
                        : (value) => setState(() {
                            _minutes = value ?? _minutes;
                            final plan = _appliedAiPlan;
                            if (plan != null &&
                                _minutes * 60 < plan.slidesCount) {
                              _appliedAiPlan = null;
                            }
                          }),
                  ),
                  const SizedBox(height: 10),
                  DropdownButtonFormField<String>(
                    // Rebuilt after each pick so the field goes back to its prompt.
                    key: ValueKey(_musicTrackIds.length),
                    initialValue: null,
                    isExpanded: true,
                    decoration: InputDecoration(
                      labelText: 'Nhạc nền (chọn 1 hoặc nhiều bài)',
                      prefixIcon: const Icon(
                        Icons.music_note_rounded,
                        size: 20,
                        color: AppColors.brand,
                      ),
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: 14,
                        vertical: 10,
                      ),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(11),
                        borderSide: const BorderSide(color: AppColors.line),
                      ),
                      enabledBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(11),
                        borderSide: const BorderSide(color: AppColors.line),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(11),
                        borderSide: const BorderSide(
                          color: AppColors.brand,
                          width: 1.5,
                        ),
                      ),
                    ),
                    hint: const Text(
                      '+ Thêm bài nhạc từ thư viện',
                      style: TextStyle(fontSize: 13),
                    ),
                    items: [
                      for (final track in tracks)
                        DropdownMenuItem(
                          value: track.id,
                          child: Text(
                            '${track.title} (${track.durationLabel})',
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 13,
                              fontWeight: FontWeight.w500,
                            ),
                          ),
                        ),
                    ],
                    onChanged:
                        _sending || _musicTrackIds.length >= _maxMusicTracks
                        ? null
                        : (value) {
                            if (value != null) {
                              setState(() => _musicTrackIds.add(value));
                            }
                          },
                  ),
                  if (tracks.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: TextButton.icon(
                        onPressed: _sending || _suggesting
                            ? null
                            : _suggestMusic,
                        icon: _suggesting
                            ? const SizedBox.square(
                                dimension: 16,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                ),
                              )
                            : const Icon(
                                Icons.auto_awesome,
                                size: 17,
                                color: AppColors.amberDark,
                              ),
                        label: Text(
                          _suggesting
                              ? 'AI đang xem bài đăng…'
                              : _suggestions == null
                              ? 'AI gợi ý nhạc hợp bài đăng'
                              : 'Gợi ý lại',
                          style: const TextStyle(
                            fontWeight: FontWeight.w700,
                            fontSize: 13,
                            color: AppColors.amberDark,
                          ),
                        ),
                      ),
                    ),
                  ],
                  if (_suggestions != null && _suggestions!.items.isNotEmpty)
                    _suggestionList(_suggestions!),
                  if (_musicTrackIds.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    for (final (index, trackId) in _musicTrackIds.indexed)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 6),
                        child: Container(
                          decoration: BoxDecoration(
                            color: AppColors.surfaceSoft,
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(color: AppColors.line),
                          ),
                          padding: const EdgeInsets.symmetric(
                            horizontal: 10,
                            vertical: 6,
                          ),
                          child: Row(
                            children: [
                              Container(
                                width: 22,
                                height: 22,
                                decoration: BoxDecoration(
                                  color: AppColors.brandTint,
                                  borderRadius: BorderRadius.circular(6),
                                  border: Border.all(
                                    color: AppColors.brandBorder,
                                  ),
                                ),
                                child: Center(
                                  child: Text(
                                    '${index + 1}',
                                    style: const TextStyle(
                                      fontSize: 11,
                                      fontWeight: FontWeight.w800,
                                      color: AppColors.brandDark,
                                    ),
                                  ),
                                ),
                              ),
                              const SizedBox(width: 8),
                              Expanded(
                                child: Text(
                                  _trackLabel(tracks, trackId),
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(
                                    fontSize: 13,
                                    fontWeight: FontWeight.w600,
                                    color: AppColors.ink,
                                  ),
                                ),
                              ),
                              const SizedBox(width: 6),
                              OutlinedButton.icon(
                                onPressed:
                                    _sending ||
                                        _musicPreviewLoadingTrackId != null
                                    ? null
                                    : () => _toggleMusicPreview(trackId),
                                style: OutlinedButton.styleFrom(
                                  minimumSize: const Size(0, 32),
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 9,
                                    vertical: 5,
                                  ),
                                  foregroundColor:
                                      _musicPreviewTrackId == trackId
                                      ? Colors.white
                                      : AppColors.brandDark,
                                  backgroundColor:
                                      _musicPreviewTrackId == trackId
                                      ? AppColors.brand
                                      : AppColors.brandTint,
                                  side: const BorderSide(
                                    color: AppColors.brandBorder,
                                  ),
                                  visualDensity: VisualDensity.compact,
                                ),
                                icon: _musicPreviewLoadingTrackId == trackId
                                    ? const SizedBox.square(
                                        dimension: 14,
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2,
                                          color: AppColors.brand,
                                        ),
                                      )
                                    : Icon(
                                        _musicPreviewTrackId == trackId
                                            ? Icons.stop_rounded
                                            : Icons.play_arrow_rounded,
                                        size: 17,
                                      ),
                                label: Text(
                                  _musicPreviewLoadingTrackId == trackId
                                      ? 'Đang tải'
                                      : _musicPreviewTrackId == trackId
                                      ? 'Dừng'
                                      : 'Nghe thử',
                                  style: const TextStyle(
                                    fontSize: 11.5,
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                              ),
                              IconButton(
                                tooltip: 'Bỏ bài này',
                                icon: const Icon(
                                  Icons.close_rounded,
                                  size: 16,
                                  color: AppColors.muted,
                                ),
                                padding: EdgeInsets.zero,
                                constraints: const BoxConstraints(
                                  minWidth: 28,
                                  minHeight: 28,
                                ),
                                onPressed: _sending
                                    ? null
                                    : () => _removeTrack(index),
                              ),
                            ],
                          ),
                        ),
                      ),
                  ],
                  const SizedBox(height: 8),
                  Container(
                    decoration: BoxDecoration(
                      color: AppColors.surfaceSoft,
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: AppColors.line),
                    ),
                    padding: const EdgeInsets.symmetric(
                      horizontal: 10,
                      vertical: 8,
                    ),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Icon(
                          Icons.info_outline_rounded,
                          size: 16,
                          color: AppColors.brand,
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            _musicTrackIds.length > 1
                                ? 'Các bài phát nối tiếp theo thứ tự trên; video dài hơn thì lặp lại từ bài đầu.'
                                : _musicTrackIds.isNotEmpty
                                ? 'Bài nhạc lặp lại cho đủ độ dài video.'
                                : job.photoHasMusic
                                ? 'Chưa chọn bài nào: dùng nhạc gốc của bài đăng.'
                                : 'Chưa chọn bài nào: bài đăng không có nhạc gốc nên video sẽ im lặng.',
                            style: const TextStyle(
                              fontSize: 12,
                              color: AppColors.muted,
                              height: 1.35,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 14),
                  StudioGradientButton(
                    onPressed: _sending ? null : () => _choose('video'),
                    isLoading: _sending,
                    icon: Icons.movie_creation_outlined,
                    label: 'Tạo video',
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

const _translationProviderLabels = {
  'google': 'Google Translate',
  'translategemma': 'TranslateGemma',
  'qwen3': 'Qwen3',
  'gemini': 'Gemini',
};

const _ttsProviderLabels = {
  'edge': 'Edge TTS',
  'cit': 'CIT Voice Studio',
  'azure': 'Azure Speech',
  'elevenlabs': 'ElevenLabs',
};

String? _optionLabel(List<Option> options, String? value) {
  if (value == null) return null;
  for (final option in options) {
    if (option.value == value) return option.label;
  }
  return value;
}

String _formatDate(DateTime? value) =>
    value == null ? '—' : DateFormat('HH:mm · dd/MM/yyyy').format(value);

String _formatDuration(Duration value) {
  final hours = value.inHours;
  final minutes = value.inMinutes.remainder(60);
  final seconds = value.inSeconds.remainder(60);
  if (hours > 0) return '$hours giờ $minutes phút';
  if (minutes > 0) return '$minutes phút $seconds giây';
  return '$seconds giây';
}

class _JobInfoCard extends StatelessWidget {
  const _JobInfoCard({required this.job});

  final Job job;

  @override
  Widget build(BuildContext context) {
    final isTranslation = job.action == 'translation';
    final mode = job.mode;
    final languages = [
      _optionLabel(sourceLanguageOptions, job.sourceLanguage),
      _optionLabel(targetLanguageOptions, job.targetLanguage),
    ];
    final tts = job.ttsProvider;
    final tiktok = job.tiktokError != null
        ? 'Lỗi: ${job.tiktokError}'
        : job.tiktokPublishAt != null && job.status == 'scheduled'
        ? 'Hẹn đăng lúc ${_formatDate(job.tiktokPublishAt)}'
        : job.tiktokStatus;

    final rows = <Widget>[
      _InfoRow(
        icon: Icons.tag_rounded,
        label: 'Mã tiến trình',
        value: job.id,
        onCopy: () {
          Clipboard.setData(ClipboardData(text: job.id));
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Đã sao chép mã tiến trình')),
          );
        },
      ),
      _InfoRow(
        icon: Icons.category_outlined,
        label: 'Loại',
        value: job.actionLabel,
      ),
      _InfoRow(
        icon: Icons.layers_outlined,
        label: 'Lô xử lý',
        value: job.batchId ?? 'Video đơn',
      ),
      if (job.sourceUrl != null)
        _InfoRow(
          icon: Icons.link_rounded,
          label: 'Link nguồn',
          value: job.sourceUrl!,
          onCopy: () {
            Clipboard.setData(ClipboardData(text: job.sourceUrl!));
            ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('Đã sao chép link nguồn')),
            );
          },
        ),
      if (isTranslation && mode != null)
        _InfoRow(
          icon: Icons.tune_rounded,
          label: 'Chế độ',
          value: jobModeLabels[mode] ?? 'Chế độ $mode',
        ),
      if (languages.any((value) => value != null))
        _InfoRow(
          icon: Icons.translate_rounded,
          label: 'Ngôn ngữ',
          value: '${languages[0] ?? '—'} → ${languages[1] ?? '—'}',
        ),
      if (job.subtitleSource != null)
        _InfoRow(
          icon: Icons.subtitles_outlined,
          label: 'Nguồn phụ đề',
          value: _optionLabel(subtitleSourceOptions, job.subtitleSource)!,
        ),
      if (job.translationProvider != null)
        _InfoRow(
          icon: Icons.auto_awesome_outlined,
          label: 'AI dịch',
          value:
              _translationProviderLabels[job.translationProvider] ??
              job.translationProvider!,
        ),
      if (tts != null && (mode ?? 1) >= 2)
        _InfoRow(
          icon: Icons.record_voice_over_outlined,
          label: 'Giọng đọc',
          value: _ttsProviderLabels[tts] ?? tts,
        ),
      if (job.ownerName != null)
        _InfoRow(
          icon: Icons.person_outline_rounded,
          label: 'Người tạo',
          value: job.ownerName!,
        ),
      if (tiktok != null)
        _InfoRow(
          icon: Icons.music_note_outlined,
          label: 'TikTok',
          value: tiktok,
        ),
    ];

    return _SectionCard(
      icon: Icons.info_outline_rounded,
      title: 'Thông tin tiến trình',
      children: rows,
    );
  }
}

class _JobTimelineCard extends StatelessWidget {
  const _JobTimelineCard({required this.job});

  final Job job;

  @override
  Widget build(BuildContext context) {
    final elapsed = job.elapsed;
    final finishedLabel = switch (job.status) {
      'failed' => 'Lỗi lúc',
      'cancelled' => 'Hủy lúc',
      _ => 'Hoàn tất',
    };
    return _SectionCard(
      icon: Icons.schedule_rounded,
      title: 'Thời gian',
      children: [
        _InfoRow(
          icon: Icons.add_circle_outline_rounded,
          label: 'Tạo lúc',
          value: _formatDate(job.createdAt),
        ),
        _InfoRow(
          icon: Icons.play_circle_outline_rounded,
          label: 'Bắt đầu',
          value: job.startedAt == null
              ? (job.isActive ? 'Đang chờ đến lượt' : '—')
              : _formatDate(job.startedAt),
        ),
        if (job.finishedAt != null || !job.isActive)
          _InfoRow(
            icon: Icons.flag_outlined,
            label: finishedLabel,
            value: _formatDate(job.finishedAt),
          ),
        if (elapsed != null)
          _InfoRow(
            icon: Icons.timer_outlined,
            label: job.isActive ? 'Đã chạy' : 'Thời gian xử lý',
            value: _formatDuration(elapsed),
          ),
        if (job.updatedAt != null)
          _InfoRow(
            icon: Icons.update_rounded,
            label: 'Cập nhật',
            value: _formatDate(job.updatedAt),
          ),
      ],
    );
  }
}

class _SectionCard extends StatelessWidget {
  const _SectionCard({
    required this.icon,
    required this.title,
    required this.children,
  });

  final IconData icon;
  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return StudioCard(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 18, color: AppColors.brand),
              const SizedBox(width: 8),
              Text(
                title,
                style: const TextStyle(
                  fontSize: 14.5,
                  fontWeight: FontWeight.w800,
                  color: AppColors.ink,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          for (var i = 0; i < children.length; i++) ...[
            if (i > 0) const Divider(height: 1, color: AppColors.lineSubtle),
            children[i],
          ],
        ],
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({
    required this.icon,
    required this.label,
    required this.value,
    this.onCopy,
  });

  final IconData icon;
  final String label;
  final String value;
  final VoidCallback? onCopy;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 9),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 16, color: AppColors.muted),
          const SizedBox(width: 10),
          SizedBox(
            width: 104,
            child: Text(
              label,
              style: const TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w600,
                color: AppColors.muted,
              ),
            ),
          ),
          Expanded(
            child: Text(
              value,
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w700,
                color: AppColors.ink,
              ),
            ),
          ),
          if (onCopy != null)
            InkWell(
              onTap: onCopy,
              borderRadius: BorderRadius.circular(8),
              child: const Padding(
                padding: EdgeInsets.only(left: 8),
                child: Icon(
                  Icons.copy_rounded,
                  size: 16,
                  color: AppColors.brand,
                ),
              ),
            ),
        ],
      ),
    );
  }
}
