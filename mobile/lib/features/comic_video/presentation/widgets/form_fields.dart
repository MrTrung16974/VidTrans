import 'package:flutter/material.dart';

import '../../../../app/theme/app_theme.dart';
import '../../data/comic_repository.dart';
import 'comic_media.dart';

/// Text field bound to a value through [onChanged] (no controller to manage).
class ComicTextField extends StatelessWidget {
  const ComicTextField({
    super.key,
    required this.label,
    required this.initial,
    required this.onChanged,
    this.hint,
    this.maxLength,
    this.lines = 1,
    this.enabled = true,
  });

  final String label;
  final String initial;
  final ValueChanged<String> onChanged;
  final String? hint;
  final int? maxLength;
  final int lines;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextFormField(
        initialValue: initial,
        enabled: enabled,
        maxLength: maxLength,
        minLines: lines > 1 ? 2 : 1,
        maxLines: lines,
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          alignLabelWithHint: lines > 1,
        ),
        onChanged: onChanged,
      ),
    );
  }
}

/// Number field; invalid input keeps the previous value (the server validates ranges).
class ComicNumberField extends StatelessWidget {
  const ComicNumberField({
    super.key,
    required this.label,
    required this.initial,
    required this.onChanged,
    this.integer = false,
    this.helper,
  });

  final String label;
  final num initial;
  final ValueChanged<num> onChanged;
  final bool integer;
  final String? helper;

  @override
  Widget build(BuildContext context) {
    final text = initial == initial.roundToDouble() ? initial.toInt().toString() : initial.toString();
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextFormField(
        initialValue: text,
        keyboardType: TextInputType.numberWithOptions(decimal: !integer),
        decoration: InputDecoration(
          labelText: label,
          helperText: helper,
        ),
        onChanged: (value) {
          final parsed = integer ? int.tryParse(value.trim()) : double.tryParse(value.trim().replaceAll(',', '.'));
          if (parsed != null) onChanged(parsed);
        },
      ),
    );
  }
}

/// "Chọn file" row showing the picked file with a clear button.
class ComicFileField extends StatelessWidget {
  const ComicFileField({
    super.key,
    required this.label,
    required this.file,
    required this.extensions,
    required this.onChanged,
  });

  final String label;
  final LocalFile? file;
  final List<String> extensions;
  final ValueChanged<LocalFile?> onChanged;

  @override
  Widget build(BuildContext context) {
    final hasFile = file != null;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: InputDecorator(
        decoration: InputDecoration(
          labelText: label,
          filled: true,
          fillColor: hasFile ? AppColors.primarySoft.withValues(alpha: 0.35) : AppColors.surface,
        ),
        child: Row(children: [
          Icon(
            hasFile ? Icons.insert_drive_file_rounded : Icons.upload_file_rounded,
            size: 18,
            color: hasFile ? AppColors.primary : AppColors.textMuted,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              file?.name ?? 'Chưa chọn (${extensions.map((e) => '.$e').join(', ')})',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontWeight: hasFile ? FontWeight.w600 : FontWeight.w400,
                color: hasFile ? AppColors.textPrimary : AppColors.textSecondary,
              ),
            ),
          ),
          if (file != null)
            IconButton(
              icon: const Icon(Icons.close, size: 18),
              onPressed: () => onChanged(null),
            ),
          TextButton(
            onPressed: () async {
              final picked = await pickLocalFile(extensions: extensions);
              if (picked != null) onChanged(picked);
            },
            child: const Text('Chọn file'),
          ),
        ]),
      ),
    );
  }
}

/// Section title inside a form.
class ComicSection extends StatelessWidget {
  const ComicSection(this.title, {super.key});

  final String title;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 10, bottom: 8),
        child: Text(
          title,
          style: Theme.of(context).textTheme.titleSmall?.copyWith(
                fontWeight: FontWeight.w700,
                color: AppColors.textPrimary,
              ),
        ),
      );
}
