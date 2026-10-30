import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/download/artifact_downloader.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../domain/job.dart';
import '../../domain/job_artifacts.dart';

/// Result files of a finished job: download into the app folder, then open
/// the share sheet (save to Photos/Files, send to another app).
class JobDownloads extends ConsumerStatefulWidget {
  const JobDownloads({super.key, required this.job});

  final Job job;

  @override
  ConsumerState<JobDownloads> createState() => _JobDownloadsState();
}

class _JobDownloadsState extends ConsumerState<JobDownloads> {
  String? _active;
  double? _progress;
  CancelToken? _cancelToken;

  @override
  void dispose() {
    _cancelToken?.cancel();
    super.dispose();
  }

  Future<void> _download(JobArtifact artifact) async {
    final downloader = ref.read(artifactDownloaderProvider);
    final messenger = ScaffoldMessenger.of(context);
    final cancelToken = CancelToken();
    setState(() {
      _active = artifact.url;
      _progress = null;
      _cancelToken = cancelToken;
    });
    try {
      final file = await downloader.download(
        artifact.url,
        fileName: artifact.fileName,
        cancelToken: cancelToken,
        onProgress: (received, total) {
          if (mounted && total > 0) setState(() => _progress = received / total);
        },
      );
      if (!mounted) return;
      setState(() => _active = null);
      await downloader.share(file, text: widget.job.title);
    } catch (error) {
      if (!cancelToken.isCancelled) {
        messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
      }
    } finally {
      if (mounted) {
        setState(() {
          _active = null;
          _cancelToken = null;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final artifacts = jobArtifacts(widget.job);
    if (artifacts.isEmpty) return const SizedBox.shrink();
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
              subtitle: 'Lưu video, phụ đề hoặc âm thanh về thiết bị',
              trailing: StudioEyebrow(
                label: '${artifacts.length} tệp',
                color: AppColors.tealText,
                backgroundColor: AppColors.tealSoft,
                borderColor: AppColors.tealBorder,
              ),
            ),
            const SizedBox(height: 12),
            for (final artifact in artifacts)
              Container(
                margin: const EdgeInsets.only(bottom: 8),
                decoration: BoxDecoration(
                  color: AppColors.surfaceSubtle,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: AppColors.line),
                ),
                child: ListTile(
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                  leading: Container(
                    width: 36,
                    height: 36,
                    decoration: BoxDecoration(
                      color: AppColors.tealSoft,
                      borderRadius: BorderRadius.circular(9),
                      border: Border.all(color: AppColors.tealBorder),
                    ),
                    alignment: Alignment.center,
                    child: const Icon(
                      Icons.download_rounded,
                      size: 18,
                      color: AppColors.teal,
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
                  subtitle: _active == artifact.url
                      ? Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: LinearProgressIndicator(value: _progress),
                        )
                      : Text(
                          artifact.fileName,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 11.5,
                            color: AppColors.muted,
                          ),
                        ),
                  trailing: _active == artifact.url
                      ? IconButton(
                          tooltip: 'Hủy tải',
                          icon: const Icon(
                            Icons.close_rounded,
                            color: AppColors.red,
                          ),
                          onPressed: () => _cancelToken?.cancel(),
                        )
                      : const Icon(
                          Icons.ios_share_rounded,
                          size: 18,
                          color: AppColors.brand,
                        ),
                  enabled: _active == null || _active == artifact.url,
                  onTap: _active == null ? () => _download(artifact) : null,
                ),
              ),
          ],
        ),
      ),
    );
  }
}
