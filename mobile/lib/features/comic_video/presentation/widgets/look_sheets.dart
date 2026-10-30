import 'package:flutter/material.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../domain/comic_labels.dart';
import '../../domain/comic_models.dart';
import 'comic_media.dart';

/// Only what the user changed (web `lookChanges`): an untouched English prompt
/// is not sent, so a Vietnamese edit makes the server translate it again.
Map<String, dynamic> changedFields(Map<String, String> initial, Map<String, String> current) => {
      for (final entry in current.entries)
        if (entry.value != initial[entry.key]) entry.key: entry.value,
    };

Future<void> showLookSheet(BuildContext context, {required WidgetBuilder builder}) => showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
        child: builder(context),
      ),
    );

/// Edits a character: name, gender, species, age and the look, in Vietnamese
/// first with the English prompt as advanced detail.
class CharacterSheet extends StatefulWidget {
  const CharacterSheet({
    super.key,
    required this.character,
    required this.onSave,
    this.imagePath,
    this.hint,
    this.readOnly = false,
    this.extra,
  });

  final ComicCharacter character;
  final Future<bool> Function(Map<String, dynamic> changes) onSave;
  final String? imagePath;
  final String? hint;
  final bool readOnly;

  /// Reference pictures and their actions.
  final Widget? extra;

  @override
  State<CharacterSheet> createState() => _CharacterSheetState();
}

class _CharacterSheetState extends State<CharacterSheet> {
  late final Map<String, String> _initial = {
    'name': widget.character.name,
    'gender': widget.character.gender,
    'species': widget.character.species,
    'age': widget.character.age,
    'appearance_vi': widget.character.appearanceVi,
    'outfit_vi': widget.character.outfitVi,
    'appearance': widget.character.appearance,
    'outfit': widget.character.outfit,
  };
  late final Map<String, String> _values = Map.of(_initial);
  bool _saving = false;

  Future<void> _save() async {
    final changes = changedFields(_initial, _values);
    if (changes.isEmpty) return Navigator.pop(context);
    setState(() => _saving = true);
    final ok = await widget.onSave(changes);
    if (!mounted) return;
    setState(() => _saving = false);
    if (ok) Navigator.pop(context);
  }

  Widget _field(String key, String label, {int lines = 1, int? maxLength, String? hint}) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextFormField(
          initialValue: _values[key],
          enabled: !widget.readOnly,
          minLines: lines > 1 ? 2 : 1,
          maxLines: lines,
          maxLength: maxLength,
          decoration: InputDecoration(
            labelText: label,
            hintText: hint,
            alignLabelWithHint: lines > 1,
          ),
          onChanged: (value) => _values[key] = value,
        ),
      );

  @override
  Widget build(BuildContext context) {
    final c = widget.character;
    return ListView(
      shrinkWrap: true,
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 20),
      children: [
        Row(children: [
          Container(
            width: 68,
            height: 68,
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: AppColors.border),
              color: AppColors.surfaceMuted,
            ),
            clipBehavior: Clip.antiAlias,
            child: widget.imagePath != null
                ? AuthImage(widget.imagePath!)
                : Center(
                    child: Text(
                      c.name.isEmpty ? '?' : c.name.characters.first.toUpperCase(),
                      style: const TextStyle(
                        fontSize: 22,
                        fontWeight: FontWeight.w800,
                        color: AppColors.primary,
                      ),
                    ),
                  ),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const StudioEyebrow(label: 'HỒ SƠ NHÂN VẬT'),
                const SizedBox(height: 2),
                Text(c.name, style: Theme.of(context).textTheme.titleLarge),
                Text(
                  'id: ${c.id}${widget.hint != null ? ' · ${widget.hint}' : ''}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
        ]),
        const SizedBox(height: 16),
        _field('name', 'Tên', maxLength: 120),
        Row(children: [
          Expanded(
            child: DropdownButtonFormField<String>(
              initialValue: genderLabels.containsKey(_values['gender']) ? _values['gender'] : 'unknown',
              decoration: const InputDecoration(labelText: 'Giới tính'),
              items: [for (final e in genderLabels.entries) DropdownMenuItem(value: e.key, child: Text(e.value))],
              onChanged: widget.readOnly ? null : (value) => _values['gender'] = value ?? 'unknown',
            ),
          ),
          const SizedBox(width: 12),
          Expanded(child: _field('age', 'Tuổi', maxLength: 60, hint: '8, 25, elderly…')),
        ]),
        const SizedBox(height: 12),
        _field('species', 'Loài (tiếng Anh)', maxLength: 80, hint: 'human, black cat, dragon…'),
        _field(
          'appearance_vi',
          'Ngoại hình cố định',
          lines: 4,
          maxLength: 600,
          hint: 'Mô tả bằng tiếng Việt: khuôn mặt, tóc, mắt, dáng người…',
        ),
        _field('outfit_vi', 'Trang phục', maxLength: 400),
        ExpansionTile(
          tilePadding: EdgeInsets.zero,
          title: Text('Prompt tiếng Anh cho AI vẽ'
              '${c.appearanceVi.isNotEmpty && c.appearance.isEmpty ? ' (sẽ tự dịch khi render)' : ''}'),
          subtitle: const Text('Sửa phần tiếng Việt thì prompt tiếng Anh được dịch lại tự động.'),
          children: [
            _field('appearance', 'Appearance', lines: 3, maxLength: 600),
            _field('outfit', 'Outfit', maxLength: 400),
          ],
        ),
        const SizedBox(height: 8),
        if (!widget.readOnly)
          FilledButton.icon(
            onPressed: _saving ? null : _save,
            icon: const Icon(Icons.save_outlined, size: 18),
            label: const Text('Lưu nhân vật'),
          ),
        if (widget.extra != null) ...[
          const SizedBox(height: 16),
          const Divider(height: 1),
          const SizedBox(height: 12),
          widget.extra!,
        ],
      ],
    );
  }
}

/// Edits a planned place; its English description is what every scene set
/// there is drawn from.
class LocationSheet extends StatefulWidget {
  const LocationSheet({
    super.key,
    required this.location,
    required this.onSave,
    this.imagePath,
    this.pinned = false,
    this.readOnly = false,
    this.extra,
  });

  final ComicLocation location;
  final Future<bool> Function(Map<String, dynamic> changes) onSave;
  final String? imagePath;
  final bool pinned;
  final bool readOnly;
  final Widget? extra;

  @override
  State<LocationSheet> createState() => _LocationSheetState();
}

class _LocationSheetState extends State<LocationSheet> {
  late final Map<String, String> _initial = {
    'name': widget.location.name,
    'description_vi': widget.location.descriptionVi,
    'description': widget.location.description,
  };
  late final Map<String, String> _values = Map.of(_initial);
  bool _saving = false;

  Future<void> _save() async {
    final changes = changedFields(_initial, _values);
    if (changes.isEmpty) return Navigator.pop(context);
    setState(() => _saving = true);
    final ok = await widget.onSave(changes);
    if (!mounted) return;
    setState(() => _saving = false);
    if (ok) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final place = widget.location;
    return ListView(
      shrinkWrap: true,
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 20),
      children: [
        AspectRatio(
          aspectRatio: 16 / 9,
          child: Container(
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: AppColors.border),
              color: AppColors.surfaceMuted,
            ),
            clipBehavior: Clip.antiAlias,
            child: Stack(fit: StackFit.expand, children: [
              if (widget.imagePath != null)
                AuthImage(widget.imagePath!)
              else
                const Center(
                  child: Padding(
                    padding: EdgeInsets.all(16),
                    child: Text(
                      'Chưa có ảnh — bấm "Vẽ thử 3 cảnh" hoặc tải ảnh lên',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: AppColors.textSecondary, fontSize: 13),
                    ),
                  ),
                ),
              if (widget.pinned)
                const Positioned(
                  top: 8,
                  left: 8,
                  child: Chip(label: Text('Đã ghim'), visualDensity: VisualDensity.compact),
                ),
            ]),
          ),
        ),
        if (widget.extra != null) ...[const SizedBox(height: 10), widget.extra!],
        const SizedBox(height: 16),
        TextFormField(
          initialValue: _values['name'],
          enabled: !widget.readOnly,
          maxLength: 120,
          decoration: const InputDecoration(labelText: 'Tên bối cảnh'),
          onChanged: (value) => _values['name'] = value,
        ),
        const SizedBox(height: 8),
        TextFormField(
          initialValue: _values['description_vi'],
          enabled: !widget.readOnly,
          maxLength: 600,
          minLines: 2,
          maxLines: 5,
          decoration: const InputDecoration(
            labelText: 'Mô tả hình ảnh cố định',
            hintText: 'Mô tả bằng tiếng Việt: kiến trúc, màu sắc, đồ vật, ánh sáng…',
            alignLabelWithHint: true,
          ),
          onChanged: (value) => _values['description_vi'] = value,
        ),
        ExpansionTile(
          tilePadding: EdgeInsets.zero,
          title: Text('Prompt tiếng Anh cho AI vẽ'
              '${place.descriptionVi.isNotEmpty && place.description.isEmpty ? ' (sẽ tự dịch khi render)' : ''}'),
          children: [
            TextFormField(
              initialValue: _values['description'],
              enabled: !widget.readOnly,
              maxLength: 600,
              minLines: 2,
              maxLines: 4,
              onChanged: (value) => _values['description'] = value,
            ),
          ],
        ),
        Text('id: ${place.id}', style: Theme.of(context).textTheme.bodySmall),
        const SizedBox(height: 12),
        if (!widget.readOnly)
          FilledButton.icon(
            onPressed: _saving ? null : _save,
            icon: const Icon(Icons.save_outlined, size: 18),
            label: const Text('Lưu bối cảnh'),
          ),
      ],
    );
  }
}
