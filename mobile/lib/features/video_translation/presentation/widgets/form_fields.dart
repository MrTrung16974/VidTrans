import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../domain/options.dart';

class FormSection extends StatelessWidget {
  const FormSection({
    super.key,
    required this.title,
    required this.children,
    this.icon,
    this.stepNumber,
    this.subtitle,
    this.trailing,
    this.accentColor,
  });

  final String title;
  final IconData? icon;
  final int? stepNumber;
  final String? subtitle;
  final Widget? trailing;
  final Color? accentColor;
  final List<Widget> children;

  Color _defaultStepAccent(int? step) => switch (step) {
    1 => AppColors.brand,
    2 => AppColors.teal,
    3 => const Color(0xFF6366F1),
    4 => const Color(0xFF334155),
    _ => AppColors.brand,
  };

  @override
  Widget build(BuildContext context) {
    final resolvedAccent =
        accentColor ??
        (stepNumber != null ? _defaultStepAccent(stepNumber) : null);
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: StudioCard(
        leftAccentColor: resolvedAccent,
        padding: const EdgeInsets.fromLTRB(18, 18, 16, 18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            StudioStepHeader(
              stepNumber: stepNumber,
              icon: stepNumber == null ? icon : null,
              title: title,
              subtitle: subtitle,
              trailing: trailing,
              darkBadge: true,
            ),
            const SizedBox(height: 16),
            ..._spaced(children),
          ],
        ),
      ),
    );
  }

  static List<Widget> _spaced(List<Widget> children) => [
    for (var i = 0; i < children.length; i++) ...[
      if (i > 0) const SizedBox(height: 12),
      children[i],
    ],
  ];
}

/// Mobile-optimized bottom sheet picker bound to an outside value.
/// Displays options in a full-width bottom sheet so long voice and model
/// names are never truncated, with search filtering for lists with >= 6 items.
class SelectField extends StatelessWidget {
  const SelectField({
    super.key,
    required this.label,
    required this.value,
    required this.options,
    required this.onChanged,
    this.disabled = const {},
    this.helper,
    this.enabled = true,
  });

  final String label;
  final String value;
  final List<Option> options;
  final ValueChanged<String> onChanged;

  /// value -> reason it is unavailable.
  final Map<String, String> disabled;
  final String? helper;
  final bool enabled;

  void _showSelectSheet(BuildContext context) {
    FocusScope.of(context).unfocus();
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) => _SelectOptionsSheet(
        title: label,
        value: value,
        options: options,
        disabled: disabled,
        onSelected: (val) {
          Navigator.of(context).pop();
          onChanged(val);
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final selectedOption = options.where((o) => o.value == value).firstOrNull;
    final displayLabel = selectedOption != null
        ? (disabled[selectedOption.value]?.isNotEmpty ?? false
            ? '${selectedOption.label} — ${disabled[selectedOption.value]}'
            : selectedOption.label)
        : (value.isNotEmpty ? value : 'Chọn $label');

    return InkWell(
      key: ValueKey('$label:$value:${disabled.length}'),
      onTap: enabled && options.isNotEmpty ? () => _showSelectSheet(context) : null,
      borderRadius: BorderRadius.circular(12),
      child: InputDecorator(
        decoration: InputDecoration(
          labelText: label,
          helperText: helper,
          helperMaxLines: 3,
          suffixIcon: Icon(
            Icons.keyboard_arrow_down_rounded,
            color: enabled && options.isNotEmpty
                ? (isDark ? AppColors.darkMuted : AppColors.muted)
                : theme.disabledColor,
            size: 22,
          ),
        ),
        isEmpty: false,
        child: Text(
          displayLabel,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(
            color: enabled && options.isNotEmpty
                ? (selectedOption != null
                    ? (isDark ? AppColors.darkText : AppColors.ink)
                    : (isDark ? AppColors.darkMuted : AppColors.mutedLight))
                : theme.disabledColor,
            fontWeight: selectedOption != null ? FontWeight.w600 : FontWeight.w400,
            fontSize: 14,
          ),
        ),
      ),
    );
  }
}

class _SelectOptionsSheet extends StatefulWidget {
  const _SelectOptionsSheet({
    required this.title,
    required this.value,
    required this.options,
    required this.disabled,
    required this.onSelected,
  });

  final String title;
  final String value;
  final List<Option> options;
  final Map<String, String> disabled;
  final ValueChanged<String> onSelected;

  @override
  State<_SelectOptionsSheet> createState() => _SelectOptionsSheetState();
}

class _SelectOptionsSheetState extends State<_SelectOptionsSheet> {
  String _query = '';

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final bottomInset = MediaQuery.paddingOf(context).bottom;

    final filtered = _query.trim().isEmpty
        ? widget.options
        : widget.options.where((o) {
            final q = _query.trim().toLowerCase();
            return o.label.toLowerCase().contains(q) ||
                o.value.toLowerCase().contains(q);
          }).toList();

    return Container(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.sizeOf(context).height * 0.75,
      ),
      decoration: BoxDecoration(
        color: isDark ? AppColors.darkSurface : AppColors.surface,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(22)),
        border: Border(
          top: BorderSide(color: isDark ? AppColors.darkLine : AppColors.line),
          left: BorderSide(color: isDark ? AppColors.darkLine : AppColors.line),
          right: BorderSide(color: isDark ? AppColors.darkLine : AppColors.line),
        ),
        boxShadow: const [
          BoxShadow(
            color: Colors.black26,
            blurRadius: 20,
            offset: Offset(0, -4),
          ),
        ],
      ),
      child: SafeArea(
        top: false,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const SizedBox(height: 10),
            Center(
              child: Container(
                width: 36,
                height: 4,
                decoration: BoxDecoration(
                  color: isDark ? AppColors.darkLineStrong : AppColors.lineStrong,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            const SizedBox(height: 8),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      widget.title,
                      style: TextStyle(
                        fontSize: 16.5,
                        fontWeight: FontWeight.w800,
                        color: isDark ? AppColors.darkText : AppColors.ink,
                        letterSpacing: -0.3,
                      ),
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.close_rounded, size: 20),
                    color: isDark ? AppColors.darkMuted : AppColors.muted,
                    onPressed: () => Navigator.of(context).pop(),
                    padding: EdgeInsets.zero,
                    constraints: const BoxConstraints(minWidth: 36, minHeight: 36),
                  ),
                ],
              ),
            ),
            if (widget.options.length >= 6) ...[
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 10),
                child: TextField(
                  decoration: InputDecoration(
                    hintText: 'Tìm kiếm ${widget.title.toLowerCase()}...',
                    prefixIcon: const Icon(Icons.search_rounded, size: 20),
                    suffixIcon: _query.isNotEmpty
                        ? IconButton(
                            icon: const Icon(Icons.clear_rounded, size: 18),
                            onPressed: () => setState(() => _query = ''),
                          )
                        : null,
                    isDense: true,
                    contentPadding: const EdgeInsets.symmetric(
                      horizontal: 12,
                      vertical: 10,
                    ),
                    filled: true,
                    fillColor: isDark
                        ? AppColors.darkSurfaceSoft
                        : AppColors.surfaceSoft,
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide(
                        color: isDark ? AppColors.darkLine : AppColors.line,
                      ),
                    ),
                    enabledBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide(
                        color: isDark ? AppColors.darkLine : AppColors.line,
                      ),
                    ),
                  ),
                  onChanged: (val) => setState(() => _query = val),
                ),
              ),
            ],
            const Divider(height: 1, thickness: 1),
            Flexible(
              child: filtered.isEmpty
                  ? Padding(
                      padding: const EdgeInsets.symmetric(vertical: 32),
                      child: Center(
                        child: Text(
                          'Không có lựa chọn phù hợp',
                          style: TextStyle(
                            color: isDark ? AppColors.darkMuted : AppColors.muted,
                            fontSize: 13.5,
                          ),
                        ),
                      ),
                    )
                  : ListView.separated(
                      padding: EdgeInsets.fromLTRB(12, 8, 12, bottomInset + 12),
                      itemCount: filtered.length,
                      separatorBuilder: (_, _) => const SizedBox(height: 4),
                      itemBuilder: (context, i) {
                        final option = filtered[i];
                        final isSelected = option.value == widget.value;
                        final disabledReason = widget.disabled[option.value];
                        final isDisabled = disabledReason != null;

                        return Material(
                          color: isSelected
                              ? (isDark
                                  ? AppColors.brand.withValues(alpha: 0.18)
                                  : AppColors.brandSoft)
                              : Colors.transparent,
                          borderRadius: BorderRadius.circular(10),
                          child: InkWell(
                            borderRadius: BorderRadius.circular(10),
                            onTap: isDisabled
                                ? null
                                : () {
                                    HapticFeedback.selectionClick();
                                    widget.onSelected(option.value);
                                  },
                            child: Padding(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 12,
                                vertical: 11,
                              ),
                              child: Row(
                                children: [
                                  Icon(
                                    isSelected
                                        ? Icons.check_circle_rounded
                                        : Icons.radio_button_unchecked_rounded,
                                    size: 20,
                                    color: isSelected
                                        ? AppColors.brand
                                        : (isDisabled
                                            ? (isDark
                                                ? AppColors.darkLineStrong
                                                : AppColors.lineStrong)
                                            : (isDark
                                                ? AppColors.darkMuted
                                                : AppColors.mutedLight)),
                                  ),
                                  const SizedBox(width: 12),
                                  Expanded(
                                    child: Column(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.start,
                                      children: [
                                        Text(
                                          option.label,
                                          style: TextStyle(
                                            fontSize: 14,
                                            fontWeight: isSelected
                                                ? FontWeight.w700
                                                : FontWeight.w500,
                                            color: isDisabled
                                                ? (isDark
                                                    ? AppColors.darkMuted
                                                    : theme.disabledColor)
                                                : (isSelected
                                                    ? AppColors.brand
                                                    : (isDark
                                                        ? AppColors.darkText
                                                        : AppColors.ink)),
                                            height: 1.3,
                                          ),
                                        ),
                                        if (disabledReason != null &&
                                            disabledReason.isNotEmpty) ...[
                                          const SizedBox(height: 3),
                                          Text(
                                            disabledReason,
                                            style: TextStyle(
                                              fontSize: 11.5,
                                              color: isDark
                                                  ? AppColors.redSoft
                                                  : AppColors.red,
                                              fontWeight: FontWeight.w600,
                                            ),
                                          ),
                                        ],
                                      ],
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ),
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

class SliderField extends StatelessWidget {
  const SliderField({
    super.key,
    required this.label,
    required this.value,
    required this.min,
    required this.max,
    required this.onChanged,
    this.divisions,
    this.format,
    this.enabled = true,
  });

  final String label;
  final double value;
  final double min;
  final double max;
  final int? divisions;
  final String Function(double)? format;
  final ValueChanged<double> onChanged;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    final clamped = value.clamp(min, max).toDouble();
    final formatted = format?.call(clamped) ?? clamped.toStringAsFixed(2);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                label,
                style: const TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                  color: AppColors.inkSoft,
                ),
              ),
            ),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2.5),
              decoration: BoxDecoration(
                color: AppColors.brandSoft,
                borderRadius: BorderRadius.circular(999),
                border: Border.all(color: AppColors.brandBorder),
              ),
              child: Text(
                formatted,
                style: const TextStyle(
                  fontSize: 11.5,
                  fontWeight: FontWeight.w800,
                  color: AppColors.brandDark,
                ),
              ),
            ),
          ],
        ),
        Slider(
          value: clamped,
          min: min,
          max: max,
          divisions: divisions,
          onChanged: enabled ? onChanged : null,
        ),
      ],
    );
  }
}

/// Small palette instead of a full color picker (no extra package needed).
class ColorChoiceField extends StatelessWidget {
  const ColorChoiceField({
    super.key,
    required this.label,
    required this.value,
    required this.onChanged,
  });

  static const palette = [
    '#ffffff',
    '#ffeb3b',
    '#ffd54f',
    '#00e5ff',
    '#76ff03',
    '#ff5252',
    '#000000',
    '#1e293b',
  ];

  final String label;
  final String value;
  final ValueChanged<String> onChanged;

  static Color parse(String css) {
    final hex = css.replaceFirst('#', '');
    return Color(int.parse(hex.length == 6 ? 'ff$hex' : hex, radix: 16));
  }

  @override
  Widget build(BuildContext context) {
    final colors = palette.contains(value.toLowerCase())
        ? palette
        : [value, ...palette];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w700,
            color: AppColors.inkSoft,
          ),
        ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            for (final css in colors)
              InkWell(
                customBorder: const CircleBorder(),
                onTap: () => onChanged(css),
                child: Container(
                  width: 34,
                  height: 34,
                  decoration: BoxDecoration(
                    color: parse(css),
                    shape: BoxShape.circle,
                    border: Border.all(
                      width: css.toLowerCase() == value.toLowerCase() ? 3 : 1.2,
                      color: css.toLowerCase() == value.toLowerCase()
                          ? AppColors.brand
                          : AppColors.lineStrong,
                    ),
                    boxShadow: css.toLowerCase() == value.toLowerCase()
                        ? AppColors.brandGlowShadow
                        : null,
                  ),
                ),
              ),
          ],
        ),
      ],
    );
  }
}
