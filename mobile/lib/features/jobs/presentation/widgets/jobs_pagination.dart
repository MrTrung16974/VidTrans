import 'package:flutter/material.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../domain/job_list.dart';
import '../providers/jobs_providers.dart';

/// Page numbers to show, `null` for an ellipsis (getPaginationPages in web/js/app.js).
List<int?> paginationPages(int current, int total) {
  if (total <= 7) return [for (var i = 1; i <= total; i++) i];
  if (current <= 4) return [1, 2, 3, 4, 5, null, total];
  if (current >= total - 3) {
    return [1, null, total - 4, total - 3, total - 2, total - 1, total];
  }
  return [1, null, current - 1, current, current + 1, null, total];
}

/// "Hiển thị 1 – 10 trong số 42 tiến trình", page size and page buttons, as on the web.
class JobsPagination extends StatelessWidget {
  const JobsPagination({
    super.key,
    required this.list,
    required this.onPage,
    required this.onPageSize,
  });

  final JobList list;
  final ValueChanged<int> onPage;
  final ValueChanged<int> onPageSize;

  @override
  Widget build(BuildContext context) {
    final page = list.page;
    final totalPages = list.totalPages < 1 ? 1 : list.totalPages;
    final start = list.startIndex + 1;
    final end = list.startIndex + list.jobs.length;

    return StudioCard(
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Text.rich(
                  TextSpan(
                    style: const TextStyle(
                      fontSize: 12.5,
                      color: AppColors.inkSoft,
                    ),
                    children: [
                      const TextSpan(text: 'Hiển thị '),
                      TextSpan(
                        text: '$start – $end',
                        style: const TextStyle(fontWeight: FontWeight.w800),
                      ),
                      const TextSpan(text: ' trong số '),
                      TextSpan(
                        text: '${list.total}',
                        style: const TextStyle(fontWeight: FontWeight.w800),
                      ),
                      const TextSpan(text: ' tiến trình'),
                    ],
                  ),
                ),
              ),
              DropdownButtonHideUnderline(
                child: DropdownButton<int>(
                  value: jobPageSizes.contains(list.limit) ? list.limit : null,
                  isDense: true,
                  borderRadius: BorderRadius.circular(10),
                  style: const TextStyle(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w700,
                    color: AppColors.ink,
                  ),
                  items: [
                    for (final size in jobPageSizes)
                      DropdownMenuItem(
                        value: size,
                        child: Text('$size / trang'),
                      ),
                  ],
                  onChanged: (size) {
                    if (size != null && size != list.limit) onPageSize(size);
                  },
                ),
              ),
            ],
          ),
          if (totalPages > 1) ...[
            const SizedBox(height: 10),
            // Seven page buttons plus four arrows: shrink rather than overflow on narrow phones.
            FittedBox(
              fit: BoxFit.scaleDown,
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  _NavButton(
                    icon: Icons.keyboard_double_arrow_left_rounded,
                    tooltip: 'Trang đầu',
                    onTap: page > 1 ? () => onPage(1) : null,
                  ),
                  _NavButton(
                    icon: Icons.chevron_left_rounded,
                    tooltip: 'Trang trước',
                    onTap: page > 1 ? () => onPage(page - 1) : null,
                  ),
                  for (final number in paginationPages(page, totalPages))
                    number == null
                        ? const SizedBox(
                            width: 22,
                            child: Text(
                              '…',
                              textAlign: TextAlign.center,
                              style: TextStyle(color: AppColors.muted),
                            ),
                          )
                        : _PageButton(
                            number: number,
                            current: number == page,
                            onTap: () => onPage(number),
                          ),
                  _NavButton(
                    icon: Icons.chevron_right_rounded,
                    tooltip: 'Trang sau',
                    onTap: page < totalPages ? () => onPage(page + 1) : null,
                  ),
                  _NavButton(
                    icon: Icons.keyboard_double_arrow_right_rounded,
                    tooltip: 'Trang cuối',
                    onTap: page < totalPages ? () => onPage(totalPages) : null,
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _NavButton extends StatelessWidget {
  const _NavButton({required this.icon, required this.tooltip, this.onTap});

  final IconData icon;
  final String tooltip;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => IconButton(
    icon: Icon(icon, size: 20),
    tooltip: tooltip,
    onPressed: onTap,
    visualDensity: VisualDensity.compact,
    constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
    padding: EdgeInsets.zero,
  );
}

class _PageButton extends StatelessWidget {
  const _PageButton({
    required this.number,
    required this.current,
    required this.onTap,
  });

  final int number;
  final bool current;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: 2),
    child: Material(
      color: current ? AppColors.brand : Colors.transparent,
      borderRadius: BorderRadius.circular(9),
      child: InkWell(
        borderRadius: BorderRadius.circular(9),
        onTap: current ? null : onTap,
        child: Container(
          constraints: const BoxConstraints(minWidth: 32),
          height: 32,
          alignment: Alignment.center,
          padding: const EdgeInsets.symmetric(horizontal: 6),
          child: Text(
            '$number',
            style: TextStyle(
              fontSize: 13,
              fontWeight: FontWeight.w800,
              color: current ? Colors.white : AppColors.inkSoft,
            ),
          ),
        ),
      ),
    ),
  );
}
