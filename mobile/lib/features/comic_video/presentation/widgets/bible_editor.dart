import 'package:flutter/material.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../domain/comic_labels.dart';
import '../../domain/story_bible.dart';

/// Review of the AI-proposed bible, in Vietnamese: the world, characters and
/// places. Edits go straight into [draft].
class BibleEditor extends StatefulWidget {
  const BibleEditor({super.key, required this.draft, this.withWorld = true});

  final BibleDraft draft;

  /// Off for episodes: the world belongs to the whole series.
  final bool withWorld;

  @override
  State<BibleEditor> createState() => _BibleEditorState();
}

class _BibleEditorState extends State<BibleEditor> {
  BibleDraft get draft => widget.draft;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (widget.withWorld) ...[
          StudioCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const StudioEyebrow(label: 'BỐI CẢNH THẾ GIỚI'),
                const SizedBox(height: 8),
                TextFormField(
                  initialValue: draft.worldVi,
                  maxLength: 600,
                  maxLines: 3,
                  minLines: 2,
                  decoration: const InputDecoration(
                    labelText: 'Thế giới & thời đại',
                    hintText: 'Ví dụ: Trung Quốc thời Đường, cung điện mái ngói đỏ, tông màu ấm',
                    alignLabelWithHint: true,
                  ),
                  onChanged: draft.setWorldVi,
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
        ],
        _Header(
          title: 'Nhân vật (${draft.characters.length})',
          action: '+ Thêm nhân vật',
          onAdd: () => setState(draft.addCharacter),
        ),
        if (draft.characters.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Text('Chưa có nhân vật — thêm hoặc để AI tự nhận ra khi chia cảnh.', style: text.bodySmall),
          ),
        for (final item in draft.characters)
          _CharacterCard(
            key: ObjectKey(item),
            item: item,
            onRemove: () => setState(() => draft.characters.remove(item)),
          ),
        const SizedBox(height: 16),
        _Header(
          title: 'Bối cảnh (${draft.locations.length})',
          action: '+ Thêm bối cảnh',
          onAdd: () => setState(draft.addLocation),
        ),
        if (draft.locations.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Text('Chưa có bối cảnh.', style: text.bodySmall),
          ),
        for (final item in draft.locations)
          _LocationCard(
            key: ObjectKey(item),
            item: item,
            onRemove: () => setState(() => draft.locations.remove(item)),
          ),
      ],
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.title, required this.action, required this.onAdd});

  final String title;
  final String action;
  final VoidCallback onAdd;

  @override
  Widget build(BuildContext context) {
    return Row(children: [
      Expanded(
        child: Text(
          title,
          style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
        ),
      ),
      TextButton.icon(
        onPressed: onAdd,
        icon: const Icon(Icons.add_circle_outline, size: 16),
        label: Text(action),
      ),
    ]);
  }
}

class _StatusBadge extends StatelessWidget {
  const _StatusBadge(this.status);

  final Object? status;

  @override
  Widget build(BuildContext context) {
    if (status != 'new' && status != 'known') return const SizedBox.shrink();
    final isNew = status == 'new';
    return Padding(
      padding: const EdgeInsets.only(left: 6),
      child: StudioStatusPill(
        label: isNew ? 'Mới' : 'Đã có',
        tone: isNew ? StudioStatusTone.brand : StudioStatusTone.neutral,
        showDot: false,
      ),
    );
  }
}

class _EnglishPrompt extends StatelessWidget {
  const _EnglishPrompt(this.value);

  final String value;

  @override
  Widget build(BuildContext context) {
    if (value.isEmpty) return const SizedBox.shrink();
    return ExpansionTile(
      tilePadding: EdgeInsets.zero,
      title: Text('Prompt tiếng Anh (tự dịch)', style: Theme.of(context).textTheme.bodySmall),
      children: [
        Align(
          alignment: Alignment.centerLeft,
          child: Text(value, style: Theme.of(context).textTheme.bodySmall),
        ),
      ],
    );
  }
}

class _CharacterCard extends StatelessWidget {
  const _CharacterCard({super.key, required this.item, required this.onRemove});

  final Map<String, dynamic> item;
  final VoidCallback onRemove;

  void _set(String field, String value) => BibleDraft.setField(item, field, value);

  @override
  Widget build(BuildContext context) {
    final english = [item['appearance'], item['outfit']]
        .where((value) => value != null && value.toString().isNotEmpty)
        .join(' · ');
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: StudioCard(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(children: [
              Expanded(
                child: TextFormField(
                  initialValue: item['name']?.toString() ?? '',
                  maxLength: 120,
                  decoration: const InputDecoration(labelText: 'Tên nhân vật', counterText: ''),
                  onChanged: (value) => _set('name', value),
                ),
              ),
              _StatusBadge(item['status']),
              IconButton(
                tooltip: 'Bỏ nhân vật này',
                icon: const Icon(Icons.close, color: AppColors.textMuted),
                onPressed: onRemove,
              ),
            ]),
            const SizedBox(height: 10),
            Row(children: [
              Expanded(
                child: DropdownButtonFormField<String>(
                  initialValue: genderLabels.containsKey(item['gender']) ? item['gender'] as String : 'unknown',
                  decoration: const InputDecoration(labelText: 'Giới tính'),
                  items: [
                    for (final entry in genderLabels.entries)
                      DropdownMenuItem(value: entry.key, child: Text(entry.value)),
                  ],
                  onChanged: (value) => _set('gender', value ?? 'unknown'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: TextFormField(
                  initialValue: item['age']?.toString() ?? '',
                  maxLength: 60,
                  decoration: const InputDecoration(labelText: 'Tuổi', counterText: ''),
                  onChanged: (value) => _set('age', value),
                ),
              ),
            ]),
            const SizedBox(height: 10),
            TextFormField(
              initialValue: item['appearance_vi']?.toString() ?? '',
              maxLength: 600,
              minLines: 2,
              maxLines: 4,
              decoration: const InputDecoration(
                labelText: 'Ngoại hình',
                hintText: 'Khuôn mặt, tóc, mắt, dáng người, dấu hiệu dễ nhận ra',
                alignLabelWithHint: true,
              ),
              onChanged: (value) => _set('appearance_vi', value),
            ),
            const SizedBox(height: 10),
            TextFormField(
              initialValue: item['outfit_vi']?.toString() ?? '',
              maxLength: 400,
              decoration: const InputDecoration(labelText: 'Trang phục thường mặc'),
              onChanged: (value) => _set('outfit_vi', value),
            ),
            _EnglishPrompt(english),
          ],
        ),
      ),
    );
  }
}

class _LocationCard extends StatelessWidget {
  const _LocationCard({super.key, required this.item, required this.onRemove});

  final Map<String, dynamic> item;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: StudioCard(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(children: [
              Expanded(
                child: TextFormField(
                  initialValue: item['name']?.toString() ?? '',
                  maxLength: 120,
                  decoration: const InputDecoration(labelText: 'Tên bối cảnh', counterText: ''),
                  onChanged: (value) => BibleDraft.setField(item, 'name', value),
                ),
              ),
              _StatusBadge(item['status']),
              IconButton(
                tooltip: 'Bỏ bối cảnh này',
                icon: const Icon(Icons.close, color: AppColors.textMuted),
                onPressed: onRemove,
              ),
            ]),
            const SizedBox(height: 10),
            TextFormField(
              initialValue: item['description_vi']?.toString() ?? '',
              maxLength: 600,
              minLines: 2,
              maxLines: 4,
              decoration: const InputDecoration(
                labelText: 'Mô tả',
                hintText: 'Kiến trúc/cảnh quan, vật liệu, màu sắc, đồ vật cố định, ánh sáng',
                alignLabelWithHint: true,
              ),
              onChanged: (value) => BibleDraft.setField(item, 'description_vi', value),
            ),
            _EnglishPrompt(item['description']?.toString() ?? ''),
          ],
        ),
      ),
    );
  }
}
