import 'dart:math' as math;

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
    BulkJobAction.pause: Icons.pause_rounded,
    BulkJobAction.resume: Icons.play_arrow_rounded,
    BulkJobAction.cancel: Icons.close_rounded,
    BulkJobAction.retry: Icons.replay_rounded,
    BulkJobAction.delete: Icons.delete_outline_rounded,
  };

  @override
  Widget build(BuildContext context) {
    final allSelected = visibleCount > 0 && selected.length >= visibleCount;
    return Material(
      color: Colors.transparent,
      elevation: 0,
      child: Container(
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: const BorderRadius.vertical(top: Radius.circular(20)),
          border: Border.all(color: AppColors.line),
          boxShadow: const [
            BoxShadow(
              color: Color(0x1F0F172A),
              blurRadius: 16,
              offset: Offset(0, -4),
            ),
          ],
        ),
        child: SafeArea(
          top: false,
          child: Padding(
            // When selection mode is active, the floating dock is hidden,
            // so JobBulkBar docks cleanly to the bottom with safe area padding.
            padding: EdgeInsets.fromLTRB(
              12,
              10,
              12,
              math.max(12.0, MediaQuery.paddingOf(context).bottom + 6.0),
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  children: [
                    IconButton(
                      tooltip: 'Thoát chế độ chọn',
                      icon: const Icon(Icons.close_rounded, size: 20),
                      visualDensity: VisualDensity.compact,
                      onPressed: busy ? null : onClose,
                    ),
                    const SizedBox(width: 4),
                    Expanded(
                      child: Row(
                        children: [
                          Flexible(
                            child: Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 10,
                                vertical: 4,
                              ),
                              decoration: BoxDecoration(
                                color: selected.isEmpty
                                    ? AppColors.surfaceMuted
                                    : AppColors.brandTint,
                                borderRadius: BorderRadius.circular(999),
                                border: Border.all(
                                  color: selected.isEmpty
                                      ? AppColors.line
                                      : AppColors.brandBorder,
                                ),
                              ),
                              child: Text(
                                selected.isEmpty
                                    ? 'Chưa chọn job'
                                    : 'Đã chọn ${selected.length} tiến trình',
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(
                                  fontWeight: FontWeight.w800,
                                  fontSize: 13,
                                  color: selected.isEmpty
                                      ? AppColors.muted
                                      : AppColors.brandDark,
                                ),
                              ),
                            ),
                          ),
                        ],
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
                        style: TextButton.styleFrom(
                          visualDensity: VisualDensity.compact,
                          foregroundColor: AppColors.brand,
                          textStyle: const TextStyle(
                            fontSize: 13,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        child: Text(
                          allSelected ? 'Bỏ chọn tất cả' : 'Chọn tất cả',
                        ),
                      ),
                  ],
                ),
                const SizedBox(height: 6),
                // Row 1: State control buttons (Pause, Resume, Cancel) - 3 equal columns for all screen widths
                Row(
                  children: [
                    Expanded(
                      child: _actionButton(
                        BulkJobAction.pause,
                        BulkJobAction.pause.eligible(selected).length,
                      ),
                    ),
                    const SizedBox(width: 6),
                    Expanded(
                      child: _actionButton(
                        BulkJobAction.resume,
                        BulkJobAction.resume.eligible(selected).length,
                      ),
                    ),
                    const SizedBox(width: 6),
                    Expanded(
                      child: _actionButton(
                        BulkJobAction.cancel,
                        BulkJobAction.cancel.eligible(selected).length,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                // Row 2: Execution buttons (Retry, Delete) - 2 equal columns for all screen widths
                Row(
                  children: [
                    Expanded(
                      child: _actionButton(
                        BulkJobAction.retry,
                        BulkJobAction.retry.eligible(selected).length,
                        accent: true,
                      ),
                    ),
                    const SizedBox(width: 6),
                    Expanded(
                      child: _actionButton(
                        BulkJobAction.delete,
                        BulkJobAction.delete.eligible(selected).length,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _actionButton(BulkJobAction action, int count, {bool accent = false}) {
    final destructive = action == BulkJobAction.delete;
    final onPressed = busy || count == 0 ? null : () => onAction(action);
    final label = Text(
      count > 0 ? '${action.label} ($count)' : action.label,
      maxLines: 1,
    );
    final style = OutlinedButton.styleFrom(
      minimumSize: const Size(0, 38),
      padding: const EdgeInsets.symmetric(horizontal: 4),
      foregroundColor: destructive
          ? AppColors.redDark
          : accent && onPressed != null
              ? AppColors.brand
              : null,
      side: destructive && onPressed != null
          ? const BorderSide(color: AppColors.redBorder)
          : accent && onPressed != null
              ? const BorderSide(color: AppColors.brandBorder, width: 1.2)
              : null,
      backgroundColor: accent && onPressed != null
          ? AppColors.brandSoft
          : destructive && onPressed != null
              ? AppColors.redSoft
              : null,
    );
    return OutlinedButton.icon(
      style: style,
      onPressed: onPressed,
      icon: Icon(_icons[action], size: 16),
      label: FittedBox(fit: BoxFit.scaleDown, child: label),
    );
  }
}
