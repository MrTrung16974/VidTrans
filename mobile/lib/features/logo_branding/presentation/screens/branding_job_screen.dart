import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/download/artifact_downloader.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../data/branding_repository.dart';
import '../../domain/branding_models.dart';
import '../providers/branding_providers.dart';
import '../widgets/branding_widgets.dart';

class BrandingJobScreen extends ConsumerWidget {
  const BrandingJobScreen({super.key, required this.jobId});

  final String jobId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final job = ref.watch(brandingJobProvider(jobId));
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: StudioSubBar(
        title: 'Chi tiết video gắn logo',
        subtitle:
            'Job #${jobId.substring(0, jobId.length.clamp(0, 8)).toUpperCase()}',
      ),
      body: job.when(
        loading: () => const Center(
          child: CircularProgressIndicator(color: AppColors.brand),
        ),
        error: (error, _) => ErrorView(
          message: errorMessage(error),
          onRetry: () => ref.invalidate(brandingJobProvider(jobId)),
        ),
        data: (job) => _BrandingJobDetail(job: job),
      ),
    );
  }
}

class _BrandingJobDetail extends ConsumerStatefulWidget {
  const _BrandingJobDetail({required this.job});

  final BrandingJob job;

  @override
  ConsumerState<_BrandingJobDetail> createState() => _BrandingJobDetailState();
}

class _BrandingJobDetailState extends ConsumerState<_BrandingJobDetail> {
  double? _download;

  BrandingJob get job => widget.job;

  Future<void> _run(Future<void> Function() action, String done) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await action();
      messenger.showSnackBar(SnackBar(content: Text(done)));
      ref.invalidate(brandingJobProvider(job.id));
      ref.invalidate(brandingHistoryProvider);
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    }
  }

  Future<void> _downloadAndShare() async {
    final downloader = ref.read(artifactDownloaderProvider);
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _download = 0);
    try {
      final name = job.title.replaceAll(RegExp(r'\.[^.]+$'), '');
      final file = await downloader.download(
        ref.read(brandingRepositoryProvider).downloadPath(job.id),
        fileName: '${name}_logo.mp4',
        onProgress: (received, total) {
          if (total > 0 && mounted) {
            setState(() => _download = received / total);
          }
        },
      );
      await downloader.share(file);
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    } finally {
      if (mounted) setState(() => _download = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final repo = ref.read(brandingRepositoryProvider);
    final readOnly = ref.watch(sessionProvider).valueOrNull?.isReadOnly ?? true;
    final placement = job.placementText;

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 32),
      children: [
        StudioCard(
          topAccentColor: AppColors.brand,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                job.title,
                style: const TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w800,
                  color: AppColors.ink,
                ),
              ),
              const SizedBox(height: 10),
              BrandingStatusChip(job.status),
              if (job.isActive) ...[
                const SizedBox(height: 14),
                LinearProgressIndicator(value: job.progress),
                const SizedBox(height: 8),
                Text(
                  '${(job.progress * 100).round()}% · ${job.stepDetail ?? ''}',
                  style: const TextStyle(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w600,
                    color: AppColors.inkSoft,
                  ),
                ),
              ],
              if (placement != null) ...[
                const SizedBox(height: 12),
                Text(
                  placement,
                  style: const TextStyle(
                    fontSize: 13,
                    color: AppColors.muted,
                  ),
                ),
              ],
            ],
          ),
        ),
        if (job.error != null) ...[
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: AppColors.redSoft,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: AppColors.redBorder),
            ),
            child: Text(
              job.error!,
              style: const TextStyle(
                color: AppColors.redText,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ],
        if (job.needsReview && !readOnly) ...[
          const SizedBox(height: 14),
          StudioCard(
            topAccentColor: AppColors.amber,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Cần xác nhận vị trí logo. Chọn lại:',
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w800,
                    color: AppColors.ink,
                  ),
                ),
                const SizedBox(height: 10),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final position in logoPositions)
                      ActionChip(
                        label: Text(positionLabels[position]!),
                        onPressed: () => _run(
                          () => repo.reprocess(job.id, override: position),
                          'Đã đưa video vào hàng đợi xử lý lại',
                        ),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ],
        const SizedBox(height: 18),
        if (job.canDownload)
          _download != null
              ? Column(
                  children: [
                    LinearProgressIndicator(value: _download),
                    const SizedBox(height: 8),
                    Text('Đang tải ${((_download ?? 0) * 100).round()}%'),
                  ],
                )
              : StudioGradientButton(
                  onPressed: _downloadAndShare,
                  icon: Icons.download_rounded,
                  label: 'Tải video và chia sẻ',
                ),
        if (!readOnly) ...[
          const SizedBox(height: 10),
          if (job.canReprocess)
            OutlinedButton.icon(
              onPressed: () => _run(
                () => repo.reprocess(job.id),
                'Đã đưa video vào hàng đợi xử lý lại',
              ),
              icon: const Icon(Icons.refresh_rounded),
              label: const Text('Xử lý lại'),
            ),
          if (job.canCancel) ...[
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: () =>
                  _run(() => repo.cancel(job.id), 'Đã yêu cầu hủy'),
              icon: const Icon(
                Icons.stop_circle_outlined,
                color: AppColors.red,
              ),
              label: const Text(
                'Hủy',
                style: TextStyle(color: AppColors.red),
              ),
            ),
          ],
        ],
      ],
    );
  }
}
