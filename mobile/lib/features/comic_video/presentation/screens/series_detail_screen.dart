import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../data/comic_repository.dart';
import '../../domain/comic_models.dart';
import '../providers/comic_providers.dart';
import '../widgets/comic_media.dart';
import '../widgets/look_sheets.dart';
import 'comic_home_screen.dart';

/// One story (series): its episodes, the visual plan, characters and places
/// shared by every episode.
class SeriesDetailScreen extends ConsumerWidget {
  const SeriesDetailScreen({super.key, required this.seriesId});

  final String seriesId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final series = ref.watch(seriesDetailProvider(seriesId));
    return series.when(
      loading: () => const Scaffold(body: Center(child: CircularProgressIndicator())),
      error: (error, _) => Scaffold(
        appBar: AppBar(),
        body: ErrorView(message: errorMessage(error), onRetry: () => ref.invalidate(seriesDetailProvider(seriesId))),
      ),
      data: (series) => _SeriesView(series: series),
    );
  }
}

class _SeriesView extends ConsumerWidget {
  const _SeriesView({required this.series});

  final SeriesDetail series;

  SeriesController _controller(WidgetRef ref) => ref.read(seriesDetailProvider(series.id).notifier);

  ComicRepository _repo(WidgetRef ref) => ref.read(comicRepositoryProvider);

  Future<bool> _apply(BuildContext context, WidgetRef ref, Future<SeriesDetail> Function() edit, String done) =>
      runComicAction(context, () => _controller(ref).apply(edit()), done: done);

  Future<void> _rename(BuildContext context, WidgetRef ref) async {
    final title = TextEditingController(text: series.summary.title);
    final description = TextEditingController(text: series.summary.description);
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Sửa truyện'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(controller: title, maxLength: 200, decoration: const InputDecoration(labelText: 'Tên truyện')),
          const SizedBox(height: 8),
          TextField(
            controller: description,
            maxLength: 20000,
            minLines: 2,
            maxLines: 5,
            decoration: const InputDecoration(labelText: 'Giới thiệu truyện'),
          ),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Hủy')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Lưu')),
        ],
      ),
    );
    if (ok != true || title.text.trim().isEmpty || !context.mounted) return;
    await _apply(
      context,
      ref,
      () => _repo(ref).patchSeries(series.id, {'title': title.text.trim(), 'description': description.text}),
      'Đã cập nhật truyện',
    );
  }

  Future<void> _delete(BuildContext context, WidgetRef ref) async {
    var withEpisodes = false;
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setLocal) => AlertDialog(
          title: Text('Xóa "${series.summary.title}"?'),
          content: Column(mainAxisSize: MainAxisSize.min, children: [
            const Text('Hồ sơ nhân vật, bối cảnh và cài đặt của truyện sẽ bị xóa.'),
            if (series.episodes.isNotEmpty)
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                value: withEpisodes,
                onChanged: (v) => setLocal(() => withEpisodes = v ?? false),
                title: Text('Xóa cả ${series.episodes.length} tập video'),
                subtitle: const Text('Bỏ chọn: các tập được giữ lại thành video lẻ'),
              ),
          ]),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Hủy')),
            FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Xóa')),
          ],
        ),
      ),
    );
    if (ok != true || !context.mounted) return;
    final deleted = await runComicAction(context, () async {
      await _repo(ref).deleteSeries(series.id, withEpisodes: withEpisodes);
      ref.invalidate(comicSeriesListProvider);
      ref.invalidate(comicProjectsProvider);
    }, done: 'Đã xóa truyện');
    if (deleted && context.mounted) context.pop();
  }

  void _openCharacter(BuildContext context, WidgetRef ref, ComicCharacter c, bool readOnly) {
    showLookSheet(
      context,
      builder: (sheetContext) => CharacterSheet(
        character: c,
        readOnly: readOnly,
        imagePath: c.referenceUrl,
        hint: c.referenceUrl != null
            ? 'ảnh tham chiếu đang ghim cho các tập'
            : 'chưa có ảnh tham chiếu — sẽ lấy ảnh của tập đầu tiên vẽ nhân vật này',
        onSave: (changes) =>
            _apply(sheetContext, ref, () => _repo(ref).patchSeriesCharacter(series.id, c.id, changes), 'Đã lưu nhân vật'),
        extra: readOnly
            ? null
            : Wrap(spacing: 8, children: [
                OutlinedButton.icon(
                  icon: const Icon(Icons.upload_outlined),
                  label: const Text('Tải ảnh tham chiếu'),
                  onPressed: () async {
                    final picture = await pickPicture();
                    if (picture == null || !sheetContext.mounted) return;
                    final ok = await _apply(
                      sheetContext,
                      ref,
                      () => _repo(ref).uploadSeriesReference(series.id, c.id, picture),
                      'Đã ghim ảnh tham chiếu',
                    );
                    if (ok && sheetContext.mounted) Navigator.pop(sheetContext);
                  },
                ),
                if (c.referenceUrl != null)
                  OutlinedButton(
                    onPressed: () async {
                      final ok = await _apply(
                        sheetContext,
                        ref,
                        () => _repo(ref).clearSeriesReference(series.id, c.id),
                        'Đã bỏ ghim ảnh',
                      );
                      if (ok && sheetContext.mounted) Navigator.pop(sheetContext);
                    },
                    child: const Text('Bỏ ghim ảnh'),
                  ),
              ]),
      ),
    );
  }

  void _openLocation(BuildContext context, WidgetRef ref, ComicLocation place, bool readOnly) {
    showLookSheet(
      context,
      builder: (sheetContext) => LocationSheet(
        location: place,
        readOnly: readOnly,
        imagePath: place.referenceUrl,
        pinned: place.referenceUrl != null,
        onSave: (changes) => _apply(
          sheetContext,
          ref,
          () => _repo(ref).patchSeriesLocation(series.id, place.id, changes),
          'Đã lưu bối cảnh',
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final readOnly = comicReadOnly(ref);
    final options = ref.watch(comicOptionsProvider).valueOrNull;
    final settings = series.settings;
    final meta = [
      if (options != null) options.styleLabel(settings['style']?.toString()),
      settings['video_mode'] == 'animated' ? 'Hoạt hình AI' : 'Truyện tranh',
      ?settings['aspect_ratio']?.toString(),
      '${series.characters.length} nhân vật',
    ].where((item) => item.isNotEmpty).join(' · ');
    final text = Theme.of(context).textTheme;

    return Scaffold(
      appBar: AppBar(
        title: Text(series.summary.title),
        actions: [
          if (!readOnly)
            PopupMenuButton<String>(
              onSelected: (value) => value == 'rename' ? _rename(context, ref) : _delete(context, ref),
              itemBuilder: (_) => const [
                PopupMenuItem(value: 'rename', child: Text('Sửa tên & giới thiệu')),
                PopupMenuItem(value: 'delete', child: Text('Xóa truyện')),
              ],
            ),
        ],
      ),
      floatingActionButton: readOnly
          ? null
          : FloatingActionButton.extended(
              onPressed: () => context.push('/comic/series/${series.id}/episode'),
              icon: const Icon(Icons.add),
              label: Text('Thêm tập ${series.nextEpisodeNumber}'),
            ),
      body: RefreshIndicator(
        onRefresh: () => ref.read(seriesDetailProvider(series.id).notifier).reload(),
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
          children: [
            StudioCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const StudioEyebrow(label: 'BỘ TRUYỆN'),
                  const SizedBox(height: 6),
                  Text(
                    '${series.summary.episodes} tập · $meta',
                    style: text.bodyMedium?.copyWith(fontWeight: FontWeight.w600, color: AppColors.textSecondary),
                  ),
                  if (series.summary.description.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    Text(series.summary.description),
                  ],
                ],
              ),
            ),
            const _Heading('Các tập'),
            if (series.episodes.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 12),
                child: Text('Chưa có tập nào. Bấm "Thêm tập" để dán nội dung tập 1.'),
              ),
            for (final job in series.episodes) ...[
              ComicJobTile(job: job, showSeries: false),
              const SizedBox(height: 8),
            ],
            const _Heading('Kế hoạch hình ảnh'),
            _BriefForm(series: series, readOnly: readOnly),
            _Heading('Nhân vật (${series.characters.length})'),
            if (series.characters.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 12),
                child: Text('Nhân vật sẽ xuất hiện ở đây sau khi tập đầu tiên được phân tích.'),
              ),
            for (final c in series.characters)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: StudioCard(
                  padding: EdgeInsets.zero,
                  child: ListTile(
                    leading: _Thumb(path: c.referenceUrl, fallback: c.name),
                    title: Text(c.name, style: const TextStyle(fontWeight: FontWeight.w700)),
                    subtitle: Text(
                      c.appearanceVi.isNotEmpty ? c.appearanceVi : c.appearance,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    trailing: const Icon(Icons.chevron_right_rounded, color: AppColors.textMuted),
                    onTap: () => _openCharacter(context, ref, c, readOnly),
                  ),
                ),
              ),
            _Heading('Bối cảnh (${series.locations.length})'),
            if (series.locations.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 12),
                child: Text('Bối cảnh được lập từ kế hoạch hình ảnh khi tập đầu tiên được phân tích.'),
              ),
            for (final place in series.locations)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: StudioCard(
                  padding: EdgeInsets.zero,
                  child: ListTile(
                    leading: _Thumb(path: place.referenceUrl, fallback: place.name),
                    title: Text(place.name, style: const TextStyle(fontWeight: FontWeight.w700)),
                    subtitle: Text(
                      place.descriptionVi.isNotEmpty ? place.descriptionVi : place.description,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    trailing: const Icon(Icons.chevron_right_rounded, color: AppColors.textMuted),
                    onTap: () => _openLocation(context, ref, place, readOnly),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _Heading extends StatelessWidget {
  const _Heading(this.title);

  final String title;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 20, bottom: 8),
        child: Text(
          title,
          style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800),
        ),
      );
}

class _Thumb extends StatelessWidget {
  const _Thumb({required this.path, required this.fallback});

  final String? path;
  final String fallback;

  @override
  Widget build(BuildContext context) => Container(
        width: 48,
        height: 48,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: AppColors.border),
          color: AppColors.surfaceMuted,
        ),
        clipBehavior: Clip.antiAlias,
        child: path != null
            ? AuthImage(path!)
            : Center(
                child: Text(
                  fallback.isEmpty ? '?' : fallback.characters.first.toUpperCase(),
                  style: const TextStyle(fontWeight: FontWeight.w800, color: AppColors.primary),
                ),
              ),
      );
}

/// The series' visual plan (story brief) and world, shared by the next episodes.
class _BriefForm extends ConsumerStatefulWidget {
  const _BriefForm({required this.series, required this.readOnly});

  final SeriesDetail series;
  final bool readOnly;

  @override
  ConsumerState<_BriefForm> createState() => _BriefFormState();
}

class _BriefFormState extends ConsumerState<_BriefForm> {
  late Map<String, String> _initial = _values();
  late final Map<String, String> _current = Map.of(_initial);

  Map<String, String> _values() => {
        'story_brief': widget.series.storyBrief,
        'world_vi': widget.series.worldVi,
        'world': widget.series.world,
      };

  Future<void> _save() async {
    final changes = changedFields(_initial, _current);
    if (changes.isEmpty) return;
    final ok = await runComicAction(context, () async {
      await ref
          .read(seriesDetailProvider(widget.series.id).notifier)
          .apply(ref.read(comicRepositoryProvider).patchSeries(widget.series.id, changes));
    }, done: 'Đã lưu kế hoạch hình ảnh');
    if (ok) setState(() => _initial = Map.of(_current));
  }

  Widget _field(String key, String label, {int lines = 3, int? maxLength, String? hint}) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextFormField(
          initialValue: _current[key],
          enabled: !widget.readOnly,
          minLines: 2,
          maxLines: lines,
          maxLength: maxLength,
          decoration: InputDecoration(
            labelText: label,
            hintText: hint,
            alignLabelWithHint: true,
          ),
          onChanged: (value) => _current[key] = value,
        ),
      );

  @override
  Widget build(BuildContext context) {
    return StudioCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _field('story_brief', 'Ghi chú cho AI (thế giới, nhân vật, bối cảnh)', lines: 6, maxLength: 6000),
          _field('world_vi', 'Thế giới & thời đại', maxLength: 600),
          ExpansionTile(
            tilePadding: EdgeInsets.zero,
            title: const Text('Prompt tiếng Anh của thế giới'),
            children: [_field('world', 'World', maxLength: 600)],
          ),
          if (!widget.readOnly)
            Align(
              alignment: Alignment.centerRight,
              child: FilledButton.tonal(onPressed: _save, child: const Text('Lưu kế hoạch')),
            ),
        ],
      ),
    );
  }
}
