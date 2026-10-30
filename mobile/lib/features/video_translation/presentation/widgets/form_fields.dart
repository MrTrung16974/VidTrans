import 'package:flutter/material.dart';

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
    final resolvedAccent = accentColor ??
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

/// Dropdown bound to an outside value. Options in [disabled] are shown with
/// their reason and cannot be picked, like disabled `<option>`s on the web.
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

  @override
  Widget build(BuildContext context) {
    final known = options.any((o) => o.value == value);
    return DropdownButtonFormField<String>(
      // Rebuild when the value changes from outside (restored settings...).
      key: ValueKey('$label:$value:${disabled.length}'),
      initialValue: known ? value : null,
      isExpanded: true,
      dropdownColor: AppColors.surface,
      borderRadius: BorderRadius.circular(12),
      decoration: InputDecoration(
        labelText: label,
        helperText: helper,
        helperMaxLines: 3,
      ),
      items: [
        for (final option in options)
          DropdownMenuItem(
            value: option.value,
            enabled: !disabled.containsKey(option.value),
            child: Text(
              disabled[option.value]?.isNotEmpty ?? false
                  ? '${option.label} — ${disabled[option.value]}'
                  : option.label,
              overflow: TextOverflow.ellipsis,
              style: disabled.containsKey(option.value)
                  ? TextStyle(color: Theme.of(context).disabledColor)
                  : null,
            ),
          ),
      ],
      onChanged:
          enabled ? (next) => next == null ? null : onChanged(next) : null,
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
