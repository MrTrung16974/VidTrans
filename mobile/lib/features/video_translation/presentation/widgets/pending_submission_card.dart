import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../providers/submission_queue.dart';

/// A started batch still being downloaded on the phone or uploaded, shown at
/// the top of the jobs list until the server creates its jobs.
class PendingSubmissionCard extends ConsumerWidget {
  const PendingSubmissionCard({super.key, required this.item});

  final PendingSubmission item;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final queue = ref.read(submissionQueueProvider.notifier);
    final percent = (item.progress * 100).round();
    final detail = switch (item.phase) {
      SubmissionPhase.waiting => 'Đang chờ đến lượt',
      SubmissionPhase.downloading =>
        item.note == null ? 'Đang chuẩn bị link…' : 'Đang tải trên máy ${item.note} · $percent%',
      SubmissionPhase.uploading => 'Đang tải lên máy chủ $percent%',
      SubmissionPhase.creating => 'Tải lên xong. Đang tạo công việc…',
      SubmissionPhase.failed => item.error ?? 'Không gửi được batch',
    };
    final indeterminate = item.phase == SubmissionPhase.waiting ||
        item.phase == SubmissionPhase.creating ||
        (item.phase == SubmissionPhase.downloading && item.note == null);

    return StudioCard(
      topAccentColor: item.failed ? AppColors.redDark : AppColors.brand,
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Icon(
                item.failed ? Icons.error_outline_rounded : Icons.downloading_rounded,
                size: 18,
                color: item.failed ? AppColors.redDark : AppColors.brand,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  '${item.name} · ${item.sourceCount} nguồn',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 13.5),
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            detail,
            style: TextStyle(
              fontSize: 12.5,
              fontWeight: FontWeight.w600,
              color: item.failed ? AppColors.redText : AppColors.inkSoft,
            ),
          ),
          if (!item.failed) ...[
            const SizedBox(height: 8),
            LinearProgressIndicator(value: indeterminate ? null : item.progress),
          ],
          const SizedBox(height: 8),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: item.failed
                ? [
                    TextButton(onPressed: () => queue.dismiss(item.id), child: const Text('Bỏ')),
                    const SizedBox(width: 6),
                    FilledButton(onPressed: () => queue.retry(item.id), child: const Text('Thử lại')),
                  ]
                : [
                    OutlinedButton(onPressed: () => queue.cancel(item.id), child: const Text('Hủy tải lên')),
                  ],
          ),
        ],
      ),
    );
  }
}
