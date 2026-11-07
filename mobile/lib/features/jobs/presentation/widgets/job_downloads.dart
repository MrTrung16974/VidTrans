import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../app/app.dart';
import '../../../../app/theme/app_theme.dart';
import '../../../../core/download/artifact_downloader.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../domain/job.dart';
import '../../domain/job_artifacts.dart';

/// Result files of a finished job: downloaded in the background by the OS
/// (survives leaving the screen or minimizing the app), saved to device
/// storage, and ready to share at any time.
class JobDownloads extends ConsumerStatefulWidget {
  const JobDownloads({super.key, required this.job});

  final Job job;

  @override
  ConsumerState<JobDownloads> createState() => _JobDownloadsState();
}

class _JobDownloadsState extends ConsumerState<JobDownloads> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _probeExisting());
  }

  @override
  void didUpdateWidget(covariant JobDownloads oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.job.id != widget.job.id) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _probeExisting());
    }
  }

  void _probeExisting() {
    if (!mounted) return;
    final controller = ref.read(artifactDownloadControllerProvider.notifier);
    for (final artifact in jobArtifacts(widget.job)) {
      unawaited(
        controller.probeCached(
          url: artifact.url,
          fileName: artifact.fileName,
          label: artifact.label,
          jobTitle: widget.job.title,
        ),
      );
    }
  }

  Future<void> _handleTap(
    JobArtifact artifact,
    ArtifactDownloadTaskState? taskState, {
    bool forceReload = false,
  }) async {
    final controller = ref.read(artifactDownloadControllerProvider.notifier);

    if (!forceReload &&
        taskState != null &&
        taskState.isCompleted &&
        taskState.filePath != null) {
      await controller.shareFile(taskState.filePath!, text: widget.job.title);
      return;
    }

    final messenger =
        scaffoldMessengerKey.currentState ?? ScaffoldMessenger.maybeOf(context);
    try {
      final file = await controller.startDownload(
        url: artifact.url,
        fileName: artifact.fileName,
        label: artifact.label,
        jobTitle: widget.job.title,
        forceReload: forceReload,
      );
      if (file == null) return;

      final isForeground =
          WidgetsBinding.instance.lifecycleState == null ||
          WidgetsBinding.instance.lifecycleState == AppLifecycleState.resumed;

      if (mounted && isForeground) {
        await controller.shareFile(file.path, text: widget.job.title);
      } else {
        messenger?.showSnackBar(
          SnackBar(
            behavior: SnackBarBehavior.floating,
            content: Text('Đã tải xong ${artifact.label} (${artifact.fileName})'),
            action: SnackBarAction(
              label: 'Chia sẻ',
              onPressed: () =>
                  controller.shareFile(file.path, text: widget.job.title),
            ),
          ),
        );
      }
    } catch (error) {
      messenger?.showSnackBar(
        SnackBar(
          behavior: SnackBarBehavior.floating,
          content: Text(errorMessage(error)),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final artifacts = jobArtifacts(widget.job);
    if (artifacts.isEmpty) return const SizedBox.shrink();
    final downloadsState = ref.watch(artifactDownloadControllerProvider);
    final controller = ref.read(artifactDownloadControllerProvider.notifier);

    return Padding(
      padding: const EdgeInsets.only(top: 14),
      child: StudioCard(
        topAccentColor: AppColors.teal,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            StudioStepHeader(
              icon: Icons.folder_zip_outlined,
              accentColor: AppColors.teal,
              title: 'Tải & chia sẻ kết quả',
              subtitle: 'Tải ngầm về thiết bị — tự tiếp tục ngay cả khi thoát màn hình',
              trailing: StudioEyebrow(
                label: '${artifacts.length} tệp',
                color: AppColors.tealText,
                backgroundColor: AppColors.tealSoft,
                borderColor: AppColors.tealBorder,
              ),
            ),
            const SizedBox(height: 12),
            for (final artifact in artifacts)
              Builder(
                builder: (context) {
                  final taskState = downloadsState[artifact.url];
                  final isDownloading = taskState?.isDownloading ?? false;
                  final isCompleted = taskState?.isCompleted ?? false;
                  final isFailed = taskState?.isFailed ?? false;
                  final percent = taskState?.progress != null
                      ? (taskState!.progress! * 100).round()
                      : null;

                  return Container(
                    margin: const EdgeInsets.only(bottom: 8),
                    decoration: BoxDecoration(
                      color: isCompleted
                          ? AppColors.greenSoft.withValues(alpha: 0.45)
                          : AppColors.surfaceSubtle,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(
                        color: isCompleted
                            ? AppColors.greenBorder
                            : (isFailed ? AppColors.redBorder : AppColors.line),
                      ),
                    ),
                    child: ListTile(
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(12),
                      ),
                      leading: Container(
                        width: 36,
                        height: 36,
                        decoration: BoxDecoration(
                          color: isCompleted
                              ? AppColors.greenSoft
                              : AppColors.tealSoft,
                          borderRadius: BorderRadius.circular(9),
                          border: Border.all(
                            color: isCompleted
                                ? AppColors.greenBorder
                                : AppColors.tealBorder,
                          ),
                        ),
                        alignment: Alignment.center,
                        child: Icon(
                          isCompleted
                              ? Icons.check_circle_rounded
                              : Icons.download_rounded,
                          size: 18,
                          color: isCompleted
                              ? AppColors.greenDark
                              : AppColors.teal,
                        ),
                      ),
                      title: Text(
                        artifact.label,
                        style: const TextStyle(
                          fontSize: 13.5,
                          fontWeight: FontWeight.w700,
                          color: AppColors.ink,
                        ),
                      ),
                      subtitle: isDownloading
                          ? Padding(
                              padding: const EdgeInsets.only(top: 6),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  LinearProgressIndicator(
                                    value: taskState?.progress,
                                  ),
                                  const SizedBox(height: 4),
                                  Text(
                                    percent != null
                                        ? 'Đang tải ngầm $percent% · Có thể thoát màn hình'
                                        : 'Đang tải ngầm · Có thể thoát màn hình',
                                    style: const TextStyle(
                                      fontSize: 11,
                                      fontWeight: FontWeight.w600,
                                      color: AppColors.tealText,
                                    ),
                                  ),
                                ],
                              ),
                            )
                          : Text(
                              isCompleted
                                  ? 'Đã lưu vào máy (${artifact.fileName}) · Chạm để mở/chia sẻ'
                                  : (isFailed
                                        ? (taskState?.error ??
                                              'Tải thất bại · Chạm để thử lại')
                                        : artifact.fileName),
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                fontSize: 11.5,
                                fontWeight: isCompleted
                                    ? FontWeight.w600
                                    : FontWeight.w500,
                                color: isCompleted
                                    ? AppColors.greenText
                                    : (isFailed
                                          ? AppColors.redText
                                          : AppColors.muted),
                              ),
                            ),
                      trailing: isDownloading
                          ? IconButton(
                              tooltip: 'Hủy tải',
                              icon: const Icon(
                                Icons.close_rounded,
                                color: AppColors.red,
                              ),
                              onPressed: () => controller.cancel(artifact.url),
                            )
                          : Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                if (isCompleted)
                                  IconButton(
                                    tooltip: 'Tải lại từ máy chủ',
                                    visualDensity: VisualDensity.compact,
                                    icon: const Icon(
                                      Icons.refresh_rounded,
                                      size: 18,
                                      color: AppColors.muted,
                                    ),
                                    onPressed: () => _handleTap(
                                      artifact,
                                      taskState,
                                      forceReload: true,
                                    ),
                                  ),
                                const Icon(
                                  Icons.ios_share_rounded,
                                  size: 18,
                                  color: AppColors.brand,
                                ),
                              ],
                            ),
                      enabled: !isDownloading,
                      onTap: isDownloading
                          ? null
                          : () => _handleTap(artifact, taskState),
                    ),
                  );
                },
              ),
          ],
        ),
      ),
    );
  }
}
