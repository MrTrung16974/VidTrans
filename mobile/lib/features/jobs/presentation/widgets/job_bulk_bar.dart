import 'package:flutter/material.dart';

import '../../../../app/theme/app_theme.dart';
import '../../domain/bulk_actions.dart';
import '../../domain/job.dart';

/// Bottom bar of the job list's selection mode, like `.bulk-actions` on the web:
/// selection count, select all, and one button per action with its eligible count.
class JobBulkBar extends StatelessWidget {
  const JobBulkBar({
    super.key,
    required this.selected,
    required this.visibleCount,
    required this.busy,
    required this.onSelectAll,
    required this.onClear,
    required this.onClose,
    required this.onAction,
  });

  final List<Job> selected;

  /// Jobs shown with the current filter and search; "select all" picks these.
  final int visibleCount;
  final bool busy;
  final VoidCallback onSelectAll;
  final VoidCallback onClear;
  final VoidCallback onClose;
  final void Function(BulkJobAction action) onAction;

  static const _icons = {
    BulkJobAction.cancel: Icons.close_rounded,
    BulkJobAction.retry: Icons.replay_rounded,
    BulkJobAction.delete: Icons.delete_outline_rounded,
  };

  @override
  Widget build(BuildContext context) {
    final allSelected = visibleCount > 0 && selected.length >= visibleCount;
    return Material(
      color: AppColors.surface,
      elevation: 8,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(8, 6, 12, 10),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                children: [
                  IconButton(
                    tooltip: 'Thoát chế độ chọn',
                    icon: const Icon(Icons.close_rounded),
                    onPressed: busy ? null : onClose,
                  ),
                  Expanded(
                    child: Text(
                      selected.isEmpty
                          ? 'Chưa chọn job'
                          : 'Đã chọn ${selected.length} tiến trình',
                      style: const TextStyle(
                        fontWeight: FontWeight.w800,
                        fontSize: 14,
                        color: AppColors.ink,
                      ),
                    ),
                  ),
                  if (busy)
                    const Padding(
                      padding: EdgeInsets.symmetric(horizontal: 12),
                      child: SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(
                          strokeWidth: 2.4,
                          color: AppColors.brand,
                        ),
                      ),
                    )
                  else
                    TextButton(
                      onPressed: visibleCount == 0
                          ? null
                          : (allSelected ? onClear : onSelectAll),
                      child: Text(
                        allSelected ? 'Bỏ chọn tất cả' : 'Chọn tất cả',
                      ),
                    ),
                ],
              ),
              Row(
                children: [
                  for (final action in BulkJobAction.values) ...[
                    if (action != BulkJobAction.values.first)
                      const SizedBox(width: 8),
                    Expanded(
                      child: _actionButton(
                        action,
                        action.eligible(selected).length,
                      ),
                    ),
                  ],
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _actionButton(BulkJobAction action, int count) {
    final destructive = action == BulkJobAction.delete;
    final onPressed = busy || count == 0 ? null : () => onAction(action);
    final label = Text(
      count > 0 ? '${action.label} ($count)' : action.label,
      maxLines: 1,
    );
    final style = OutlinedButton.styleFrom(
      minimumSize: const Size(0, 40),
      padding: const EdgeInsets.symmetric(horizontal: 8),
      foregroundColor: destructive ? AppColors.redDark : null,
      side: destructive && onPressed != null
          ? const BorderSide(color: AppColors.redBorder)
          : null,
    );
    return OutlinedButton.icon(
      style: style,
      onPressed: onPressed,
      icon: Icon(_icons[action], size: 17),
      label: FittedBox(fit: BoxFit.scaleDown, child: label),
    );
  }
}
