import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

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
  const JobDetailScreen({super.key, required this.jobId, this.askDownloadOnly = false});

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
Future<void> confirmDownloadOnly(BuildContext context, WidgetRef ref, Job job) async {
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
    messenger.showSnackBar(const SnackBar(content: Text('Đang chuẩn bị video gốc để tải về')));
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
        studioDockBottomPadding(context),
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
                if (job.status == 'completed' && !readOnly && (account?.canCutClips ?? false)) ...[
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
        if (job.awaitsPhotoChoice && !readOnly && (account?.canUsePhotoPosts ?? false))
          _PhotoPostChoiceCard(job: job),
        JobDownloads(job: job),
        const SizedBox(height: 14),
        _JobInfoCard(job: job),
        const SizedBox(height: 14),
        _JobTimelineCard(job: job),
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

String _minutesLabel(double minutes) =>
    minutes < 1 ? '${(minutes * 60).round()} giây' : '${minutes.toStringAsFixed(0)} phút';

/// A photo post waits here: download its images, or a slideshow of a chosen
/// length with the post's music or a library track.
class _PhotoPostChoiceCard extends ConsumerStatefulWidget {
  const _PhotoPostChoiceCard({required this.job});

  final Job job;

  @override
  ConsumerState<_PhotoPostChoiceCard> createState() => _PhotoPostChoiceCardState();
}

class _PhotoPostChoiceCardState extends ConsumerState<_PhotoPostChoiceCard> {
  double _minutes = 1;
  String _musicTrackId = '';
  bool _sending = false;

  Future<void> _choose(String action) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _sending = true);
    try {
      await ref.read(jobsRepositoryProvider).choosePhotoPost(
            widget.job.id,
            action: action,
            minutes: action == 'video' ? _minutes : null,
            musicTrackId: action == 'video' ? _musicTrackId : null,
          );
      messenger.showSnackBar(SnackBar(
        content: Text(action == 'images' ? 'Đang gom ảnh để tải về' : 'Đang dựng video ghép ảnh'),
      ));
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
    final tracks = ref.watch(musicTracksProvider).valueOrNull ?? const <MusicTrack>[];
    return Padding(
      padding: const EdgeInsets.only(top: 14),
      child: StudioCard(
        topAccentColor: AppColors.amber,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Bài đăng ảnh', style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800)),
            const SizedBox(height: 4),
            Text('Link này là bài đăng ${job.photoImageCount} ảnh, không phải video. Chọn cách xử lý:'),
            const SizedBox(height: 14),
            OutlinedButton.icon(
              onPressed: _sending ? null : () => _choose('images'),
              icon: const Icon(Icons.photo_library_outlined),
              label: const Text('Chỉ tải ảnh về (ZIP)'),
            ),
            const Divider(height: 28),
            const Text('Tạo video ghép ảnh có nhạc', style: TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 10),
            DropdownButtonFormField<double>(
              initialValue: _minutes,
              decoration: const InputDecoration(labelText: 'Độ dài video'),
              items: [
                for (final minutes in _slideshowMinutes)
                  DropdownMenuItem(value: minutes, child: Text(_minutesLabel(minutes))),
              ],
              onChanged: _sending ? null : (value) => setState(() => _minutes = value ?? _minutes),
            ),
            const SizedBox(height: 10),
            DropdownButtonFormField<String>(
              initialValue: _musicTrackId,
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Nhạc nền'),
              items: [
                DropdownMenuItem(
                  value: '',
                  child: Text(job.photoHasMusic ? 'Nhạc gốc của bài đăng' : 'Không có nhạc gốc (im lặng)'),
                ),
                for (final track in tracks)
                  DropdownMenuItem(value: track.id, child: Text(track.title, overflow: TextOverflow.ellipsis)),
              ],
              onChanged: _sending ? null : (value) => setState(() => _musicTrackId = value ?? ''),
            ),
            const SizedBox(height: 6),
            const Text(
              'Mỗi ảnh hiện tối đa 8 giây, có zoom nhẹ; video dài thì bộ ảnh được lặp lại cho đủ thời lượng.',
              style: TextStyle(fontSize: 12, color: AppColors.muted),
            ),
            const SizedBox(height: 12),
            FilledButton.icon(
              onPressed: _sending ? null : () => _choose('video'),
              icon: const Icon(Icons.movie_creation_outlined),
              label: const Text('Tạo video'),
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
          value: _translationProviderLabels[job.translationProvider] ??
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
                child: Icon(Icons.copy_rounded, size: 16, color: AppColors.brand),
              ),
            ),
        ],
      ),
    );
  }
}
