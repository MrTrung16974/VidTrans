import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:uuid/uuid.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/download/artifact_downloader.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../data/comic_repository.dart';
import '../../domain/comic_labels.dart';
import '../../domain/comic_models.dart';
import '../providers/comic_providers.dart';
import '../widgets/comic_media.dart';
import '../widgets/look_sheets.dart';

/// Editor of one episode/video: progress, render actions, results, and the
/// storyboard split into tabs (scenes, characters, places) for a phone.
class ProjectEditorScreen extends ConsumerWidget {
  const ProjectEditorScreen({super.key, required this.jobId});

  final String jobId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final project = ref.watch(comicProjectProvider(jobId));
    return project.when(
      loading: () => const Scaffold(body: Center(child: CircularProgressIndicator())),
      error: (error, _) => Scaffold(
        appBar: AppBar(),
        body: ErrorView(message: errorMessage(error), onRetry: () => ref.invalidate(comicProjectProvider(jobId))),
      ),
      data: (project) => _Editor(project: project),
    );
  }
}

class _Editor extends ConsumerStatefulWidget {
  const _Editor({required this.project});

  final ComicProject project;

  @override
  ConsumerState<_Editor> createState() => _EditorState();
}

class _EditorState extends ConsumerState<_Editor> {
  /// Scene numbers ticked for a bulk change.
  final _selected = <int>{};

  /// One key per render button press, kept until it succeeds, so retrying
  /// after a network error cannot queue (and bill) the same render twice.
  final _renderKeys = <String, String>{};

  ComicProject get project => widget.project;
  String get jobId => project.job.id;
  ComicProjectController get _controller => ref.read(comicProjectProvider(jobId).notifier);
  ComicRepository get _repo => ref.read(comicRepositoryProvider);

  Future<bool> _edit(Future<ComicProject> Function(ScenePage page) edit, String done) =>
      runComicAction(context, () => _controller.apply(edit), done: done);

  Future<void> _render(String action) async {
    if (action == 'reanalyze' &&
        !await confirmComic(
          context,
          'Phân tích lại sẽ thay toàn bộ storyboard hiện tại (các chỉnh sửa tay sẽ mất). Tiếp tục?',
        )) {
      return;
    }
    final body = switch (action) {
      'trial' => RenderRequest.trial(),
      'images' => RenderRequest.imagesOnly(),
      'reanalyze' => RenderRequest.reanalyze(),
      _ => RenderRequest.render(),
    };
    final key = _renderKeys.putIfAbsent(action, () => const Uuid().v4());
    if (!mounted) return;
    final ok = await runComicAction(
      context,
      () async {
        await _repo.render(jobId, body, idempotencyKey: key);
        if (action == 'reanalyze') {
          await _controller.goToPage(1);
        } else {
          await _controller.reload();
        }
      },
      done: switch (action) {
        'trial' => 'Đang vẽ thử nhân vật, bối cảnh và 3 cảnh đầu…',
        'images' => 'Đang sinh ảnh cho các cảnh đã đổi…',
        'reanalyze' => 'Đang phân tích lại truyện…',
        _ => 'Đang render — chỉ tạo lại phần đã thay đổi',
      },
    );
    if (ok) _renderKeys.remove(action);
  }

  Future<void> _download(String path, String extension) async {
    final comic = ComicJobInfo.of(project.job);
    final name = '${comic.episodeTitle().isEmpty ? jobId : comic.episodeTitle()}.$extension';
    final messenger = ScaffoldMessenger.of(context);
    messenger.showSnackBar(SnackBar(content: Text('Đang tải $name…')));
    await runComicAction(context, () async {
      final downloader = ref.read(artifactDownloaderProvider);
      final file = await downloader.download(path, fileName: name);
      messenger.hideCurrentSnackBar();
      await downloader.share(file);
    });
  }

  @override
  Widget build(BuildContext context) {
    final readOnly = comicReadOnly(ref);
    final storyboard = project.storyboard;
    final comic = ComicJobInfo.of(project.job);
    final title = comic.episodeTitle().isNotEmpty ? comic.episodeTitle() : project.job.title;
    final canEdit = !readOnly && !project.locked;

    return DefaultTabController(
      length: 3,
      child: Scaffold(
        appBar: AppBar(
          title: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis),
          actions: [
            if (comic.seriesId != null)
              IconButton(
                tooltip: comic.seriesTitle.isEmpty ? 'Truyện' : comic.seriesTitle,
                icon: const Icon(Icons.auto_stories_outlined),
                onPressed: () => context.push('/comic/series/${comic.seriesId}'),
              ),
            PopupMenuButton<String>(
              onSelected: (value) async {
                if (value == 'story') {
                  _showStory();
                } else if (value == 'reanalyze') {
                  await _render('reanalyze');
                }
              },
              itemBuilder: (_) => [
                const PopupMenuItem(value: 'story', child: Text('Xem truyện gốc')),
                if (canEdit) const PopupMenuItem(value: 'reanalyze', child: Text('Phân tích lại truyện')),
              ],
            ),
          ],
          bottom: TabBar(tabs: [
            Tab(text: 'Cảnh (${project.pagination.totalScenes})'),
            Tab(text: 'Nhân vật (${storyboard?.characters.length ?? 0})'),
            Tab(text: 'Bối cảnh (${storyboard?.locations.length ?? 0})'),
          ]),
        ),
        body: RefreshIndicator(
          onRefresh: _controller.reload,
          child: TabBarView(children: [
            _scenesTab(canEdit, readOnly),
            _charactersTab(canEdit),
            _locationsTab(canEdit),
          ]),
        ),
        bottomNavigationBar: _selected.isEmpty || storyboard == null || storyboard.animated
            ? null
            : _bulkBar(storyboard, canEdit),
      ),
    );
  }

  Future<void> _showStory() async {
    final text = await _repo.story(jobId).catchError((Object error) => errorMessage(error));
    if (!mounted) return;
    showLookSheet(
      context,
      builder: (_) => ListView(
        shrinkWrap: true,
        padding: const EdgeInsets.all(16),
        children: [SelectableText(text)],
      ),
    );
  }

  // --------------------------------------------------------------- header
  Widget _header(bool readOnly) {
    final job = project.job;
    final storyboard = project.storyboard;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final locked = project.locked;
    final animated = storyboard?.animated ?? false;
    final metrics = project.metrics;
    final meta = storyboard == null
        ? 'Storyboard đang được tạo…'
        : [
            'Bản ${storyboard.revision}',
            '${project.pagination.totalScenes} cảnh',
            if (animated) '${project.pagination.totalShots} shot',
            '${storyboard.characters.length} nhân vật',
            'chia cảnh: ${storyboard.analysisMethod}',
            if (metrics != null)
              'AI ${metrics['ai_seconds']}s · ${metrics['attempts']} lượt sinh · ${metrics['fallback_shots']} shot dự phòng 2.5D',
          ].join(' · ');
    final detail = job.error ?? job.stepDetail ?? '';

    return Padding(
      padding: const EdgeInsets.all(16),
      child: StudioCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(children: [
              Expanded(
                child: Text(
                  comicStepLabel(job.step, job.status),
                  style: text.titleMedium?.copyWith(
                    fontWeight: FontWeight.w800,
                    color: job.status == 'failed' ? scheme.error : AppColors.textPrimary,
                  ),
                ),
              ),
              if (locked && !readOnly)
                TextButton.icon(
                  onPressed: () => runComicAction(context, () => _repo.cancel(jobId), done: 'Đã yêu cầu dừng'),
                  icon: const Icon(Icons.stop_circle_outlined, color: AppColors.danger),
                  label: const Text('Dừng', style: TextStyle(color: AppColors.danger)),
                ),
            ]),
            if (locked) ...[
              const SizedBox(height: 8),
              ClipRRect(
                borderRadius: BorderRadius.circular(999),
                child: LinearProgressIndicator(value: comicProgress(job), minHeight: 6),
              ),
            ],
            if (detail.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(detail, style: TextStyle(color: job.error != null ? scheme.error : AppColors.textSecondary)),
            ],
            const SizedBox(height: 6),
            Text(meta, style: text.bodySmall),
            if (project.stale && !locked) ...[
              const SizedBox(height: 10),
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: AppColors.warningBg,
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(color: AppColors.warningBorder),
                ),
                child: const Text(
                  'Storyboard đã thay đổi so với video đã xuất — bấm "Render video" để cập nhật.',
                  style: TextStyle(color: AppColors.warning, fontWeight: FontWeight.w600, fontSize: 13),
                ),
              ),
            ],
            if (storyboard != null && storyboard.warnings.isNotEmpty)
              ExpansionTile(
                tilePadding: EdgeInsets.zero,
                title: Text('Cần kiểm tra (${storyboard.warnings.length})', style: TextStyle(color: scheme.error)),
                children: [
                  for (final warning in storyboard.warnings.take(12))
                    Align(alignment: Alignment.centerLeft, child: Text('• $warning')),
                ],
              ),
            if (job.videoUrl != null && !locked) ...[
              const SizedBox(height: 12),
              Wrap(spacing: 8, runSpacing: 8, children: [
                FilledButton.icon(
                  onPressed: () => context.push('/jobs/$jobId/player'),
                  icon: const Icon(Icons.play_arrow),
                  label: const Text('Xem video'),
                ),
                OutlinedButton.icon(
                  onPressed: () => _download(job.videoUrl!, 'mp4'),
                  icon: const Icon(Icons.download),
                  label: const Text('Tải MP4'),
                ),
                if (job.subtitleUrl != null)
                  OutlinedButton.icon(
                    onPressed: () => _download(job.subtitleUrl!, 'srt'),
                    icon: const Icon(Icons.subtitles_outlined),
                    label: const Text('Tải SRT'),
                  ),
              ]),
            ],
            if (!readOnly) ...[
              const SizedBox(height: 12),
              Wrap(spacing: 8, runSpacing: 8, children: [
                FilledButton.tonal(onPressed: locked ? null : () => _render('render'), child: const Text('Render video')),
                OutlinedButton(onPressed: locked ? null : () => _render('trial'), child: const Text('Vẽ thử 3 cảnh')),
                OutlinedButton(
                  onPressed: locked ? null : () => _render('images'),
                  child: Text(animated ? 'Chỉ sinh keyframe' : 'Chỉ sinh ảnh'),
                ),
              ]),
            ],
          ],
        ),
      ),
    );
  }

  // --------------------------------------------------------------- scenes
  Widget _scenesTab(bool canEdit, bool readOnly) {
    final storyboard = project.storyboard;
    final pagination = project.pagination;
    final ratio = (project.settings['aspect_ratio'] ?? ComicJobInfo.of(project.job).aspectRatio) == '16:9' ? 16 / 9 : 9 / 16;
    return ListView(
      padding: const EdgeInsets.only(bottom: 24),
      children: [
        _header(readOnly),
        if (storyboard == null)
          const Padding(
            padding: EdgeInsets.all(24),
            child: Center(child: Text('Storyboard đang được tạo…')),
          )
        else ...[
          for (final scene in storyboard.scenes)
            _SceneCard(
              scene: scene,
              storyboard: storyboard,
              media: project.media,
              reports: project.shotReports,
              ratio: ratio,
              selectable: canEdit && !storyboard.animated,
              selected: _selected.contains(scene.sceneIndex),
              onSelected: (value) => setState(() {
                if (value) {
                  _selected.add(scene.sceneIndex);
                } else {
                  _selected.remove(scene.sceneIndex);
                }
              }),
              onTap: () => context.push('/comic/projects/$jobId/scenes/${scene.sceneIndex}'),
            ),
          if (pagination.totalPages > 1 || pagination.totalScenes > 6) _pager(pagination),
        ],
      ],
    );
  }

  Widget _pager(ScenesPagination pagination) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: StudioCard(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        child: Row(children: [
          IconButton(
            onPressed: pagination.page > 1 ? () => _controller.goToPage(pagination.page - 1) : null,
            icon: const Icon(Icons.chevron_left),
          ),
          Expanded(
            child: Text(
              'Trang ${pagination.page}/${pagination.totalPages}',
              textAlign: TextAlign.center,
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
          ),
          IconButton(
            onPressed:
                pagination.page < pagination.totalPages ? () => _controller.goToPage(pagination.page + 1) : null,
            icon: const Icon(Icons.chevron_right),
          ),
          PopupMenuButton<int>(
            tooltip: 'Số cảnh mỗi trang',
            initialValue: pagination.limit,
            onSelected: (limit) => _controller.goToPage(1, limit: limit),
            itemBuilder: (_) => [
              for (final n in const [6, 10, 20, 40]) PopupMenuItem(value: n, child: Text('$n cảnh/trang')),
            ],
            child: Padding(
              padding: const EdgeInsets.all(8),
              child: Text('${pagination.limit}/trang', style: const TextStyle(color: AppColors.primary, fontWeight: FontWeight.w700)),
            ),
          ),
        ]),
      ),
    );
  }

  Widget _bulkBar(Storyboard storyboard, bool canEdit) {
    final scenes = _selected.toList()..sort();
    Future<void> run(Map<String, dynamic> body, String done) async {
      final ok = await _edit(
        (page) => _repo.bulkScenes(jobId, {'scene_indexes': scenes, 'draw_now': true, ...body}, page),
        done,
      );
      if (ok) setState(_selected.clear);
    }

    return SafeArea(
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: const BoxDecoration(
          color: AppColors.surface,
          border: Border(top: BorderSide(color: AppColors.border)),
        ),
        child: Row(children: [
          Expanded(
            child: Text('${scenes.length} cảnh đã chọn', style: const TextStyle(fontWeight: FontWeight.w700)),
          ),
          if (storyboard.locations.isNotEmpty)
            PopupMenuButton<String>(
              enabled: canEdit,
              tooltip: 'Đổi bối cảnh',
              onSelected: (id) => run({'location_id': id}, 'Đang vẽ lại ${scenes.length} cảnh…'),
              itemBuilder: (_) => [
                const PopupMenuItem(value: '', child: Text('— Không gắn bối cảnh')),
                for (final place in storyboard.locations) PopupMenuItem(value: place.id, child: Text(place.name)),
              ],
              child: const Padding(
                padding: EdgeInsets.all(8),
                child: Text('Đổi bối cảnh', style: TextStyle(color: AppColors.primary, fontWeight: FontWeight.w600)),
              ),
            ),
          TextButton(
            onPressed: canEdit ? () => run({'redraw': true}, 'Đang vẽ lại ${scenes.length} cảnh…') : null,
            child: const Text('Vẽ lại'),
          ),
          IconButton(
            tooltip: 'Bỏ chọn',
            onPressed: () => setState(_selected.clear),
            icon: const Icon(Icons.close),
          ),
        ]),
      ),
    );
  }

  // ----------------------------------------------------------- characters
  Widget _charactersTab(bool canEdit) {
    final storyboard = project.storyboard;
    final characters = storyboard?.characters ?? const <ComicCharacter>[];
    if (characters.isEmpty) {
      return ListView(children: const [
        Padding(padding: EdgeInsets.all(24), child: Text('Chưa có nhân vật — chờ storyboard được tạo.')),
      ]);
    }
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        for (final c in characters)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: StudioCard(
              padding: EdgeInsets.zero,
              child: ListTile(
                leading: _Thumb(path: project.media.references[c.id], fallback: c.name),
                title: Text(c.name, style: const TextStyle(fontWeight: FontWeight.w700)),
                subtitle: Text(
                  [
                    genderLabels[c.gender] ?? c.gender,
                    if (c.age.isNotEmpty) c.age,
                    if (c.approvedReference != null) 'đã ghim ảnh tham chiếu',
                  ].join(' · '),
                ),
                trailing: const Icon(Icons.chevron_right_rounded, color: AppColors.textMuted),
                onTap: () => showLookSheet(
                  context,
                  builder: (sheetContext) => CharacterSheet(
                    character: c,
                    readOnly: !canEdit,
                    imagePath: project.media.references[c.id],
                    hint: c.approvedReference != null
                        ? 'đang ghim ảnh tham chiếu đã duyệt'
                        : 'sửa ngoại hình sẽ vẽ lại ảnh tham chiếu và các cảnh có nhân vật này',
                    onSave: (changes) =>
                        _edit((page) => _repo.patchCharacter(jobId, c.id, changes, page), 'Đã lưu nhân vật'),
                    extra: _ReferenceGallery(jobId: jobId, characterId: c.id, canEdit: canEdit),
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }

  // ------------------------------------------------------------ locations
  Widget _locationsTab(bool canEdit) {
    final storyboard = project.storyboard;
    final places = storyboard?.locations ?? const <ComicLocation>[];
    final media = project.media;
    final world = storyboard == null ? '' : (storyboard.worldVi.isNotEmpty ? storyboard.worldVi : storyboard.world);
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        if (world.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: StudioCard(child: Text('Thế giới: $world')),
          ),
        if (places.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Text(
              media.placeReferencesSupported
                  ? 'Ảnh bối cảnh được gửi kèm khi vẽ mọi cảnh ở nơi đó để giữ cảnh vật nhất quán. Ghim ảnh ưng ý để giữ cố định.'
                  : 'AI vẽ đang chọn chỉ đọc mô tả chữ (ảnh bối cảnh chỉ dùng được với Gemini hoặc ComfyUI).',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
        if (places.isEmpty)
          const Padding(padding: EdgeInsets.all(12), child: Text('Chưa có bối cảnh.')),
        for (final place in places)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: StudioCard(
              padding: EdgeInsets.zero,
              child: ListTile(
                leading: _Thumb(path: media.locationReferences[place.id], fallback: place.name),
                title: Text(place.name, style: const TextStyle(fontWeight: FontWeight.w700)),
                subtitle: Text(
                  place.descriptionVi.isNotEmpty ? place.descriptionVi : place.description,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                trailing: place.approvedReference != null
                    ? const Icon(Icons.push_pin_outlined, size: 18, color: AppColors.primary)
                    : const Icon(Icons.chevron_right_rounded, color: AppColors.textMuted),
                onTap: () => showLookSheet(
                  context,
                  builder: (sheetContext) => LocationSheet(
                    location: place,
                    readOnly: !canEdit,
                    imagePath: media.locationReferences[place.id],
                    pinned: place.approvedReference != null,
                    onSave: (changes) => _edit(
                      (page) => _repo.patchLocation(jobId, place.id, changes, page),
                      'Đã lưu bối cảnh — các cảnh ở đây sẽ được vẽ lại khi render',
                    ),
                    extra: canEdit ? _placeActions(sheetContext, place) : null,
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }

  Widget _placeActions(BuildContext sheetContext, ComicLocation place) {
    final drawn = project.media.locationDrawn[place.id];
    Future<void> act(Future<ComicProject> Function(ScenePage page) edit, String done) async {
      final ok = await _edit(edit, done);
      if (ok && sheetContext.mounted) Navigator.pop(sheetContext);
    }

    return Wrap(spacing: 8, runSpacing: 8, children: [
      if (drawn != null && place.approvedReference == null)
        OutlinedButton(
          onPressed: () =>
              act((page) => _repo.approveLocationReference(jobId, place.id, drawn, page), 'Đã ghim ảnh bối cảnh'),
          child: const Text('Ghim ảnh này'),
        ),
      if (place.approvedReference != null)
        OutlinedButton(
          onPressed: () => act((page) => _repo.unpinLocationReference(jobId, place.id, page), 'Đã bỏ ghim ảnh bối cảnh'),
          child: const Text('Bỏ ghim'),
        ),
      OutlinedButton.icon(
        icon: const Icon(Icons.upload_outlined),
        label: const Text('Tải ảnh lên'),
        onPressed: () async {
          final picture = await pickPicture();
          if (picture == null) return;
          await act(
            (page) => _repo.uploadLocationReference(jobId, place.id, picture, page),
            'Đã tải và ghim ảnh bối cảnh',
          );
        },
      ),
    ]);
  }
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

class _SceneCard extends StatelessWidget {
  const _SceneCard({
    required this.scene,
    required this.storyboard,
    required this.media,
    required this.reports,
    required this.ratio,
    required this.selectable,
    required this.selected,
    required this.onSelected,
    required this.onTap,
  });

  final ComicScene scene;
  final Storyboard storyboard;
  final ComicMedia media;
  final Map<String, Map<String, dynamic>> reports;
  final double ratio;
  final bool selectable;
  final bool selected;
  final ValueChanged<bool> onSelected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final index = scene.sceneIndex;
    final firstShot = scene.shots.firstOrNull;
    final image = storyboard.animated && firstShot != null
        ? media.shotStill('$index.${firstShot.shotIndex}')
        : media.sceneImage(index);
    final place = storyboard.location(scene.locationId)?.name ?? scene.location;
    final meta = [
      'v${scene.version}',
      if (storyboard.animated) '${scene.shots.length} shot',
      if (scene.emotion.isNotEmpty) scene.emotion,
      if (place.isNotEmpty) place,
    ].join(' · ');
    final narration = storyboard.animated && scene.narration.isEmpty
        ? scene.shots.map((shot) => shot.narration).where((n) => n.isNotEmpty).join(' ')
        : scene.narration;

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 5),
      child: StudioCard(
        padding: const EdgeInsets.all(12),
        borderColor: selected ? AppColors.primary : AppColors.border,
        color: selected ? AppColors.primarySoft.withValues(alpha: 0.35) : AppColors.surface,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(10),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SizedBox(
              width: 96,
              child: AspectRatio(
                aspectRatio: ratio,
                child: Container(
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: AppColors.border),
                    color: AppColors.surfaceMuted,
                  ),
                  clipBehavior: Clip.antiAlias,
                  child: Stack(fit: StackFit.expand, children: [
                    if (image != null)
                      AuthImage(image)
                    else
                      const Center(child: Icon(Icons.image_not_supported_outlined, color: AppColors.textMuted)),
                    if (scene.imageOverride != null)
                      const Positioned(left: 4, top: 4, child: Icon(Icons.upload_file, size: 16, color: Colors.white)),
                  ]),
                ),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('Cảnh $index', style: Theme.of(context).textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800)),
                Text(meta, style: Theme.of(context).textTheme.bodySmall),
                const SizedBox(height: 6),
                Text(narration, maxLines: 4, overflow: TextOverflow.ellipsis),
                if (scene.fixNoteVi.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(
                      'Đã ghi chú sửa: ${scene.fixNoteVi}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                            fontStyle: FontStyle.italic,
                            color: AppColors.primary,
                          ),
                    ),
                  ),
              ]),
            ),
            if (selectable) Checkbox(value: selected, onChanged: (value) => onSelected(value ?? false)),
          ]),
        ),
      ),
    );
  }
}

/// Versions of a character's reference picture: approve one, upload, or unpin.
class _ReferenceGallery extends ConsumerWidget {
  const _ReferenceGallery({required this.jobId, required this.characterId, required this.canEdit});

  final String jobId;
  final String characterId;
  final bool canEdit;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final refs = ref.watch(characterReferencesProvider((jobId: jobId, characterId: characterId)));
    final controller = ref.read(comicProjectProvider(jobId).notifier);
    final repo = ref.read(comicRepositoryProvider);
    Future<void> edit(Future<ComicProject> Function(ScenePage page) call, String done) =>
        runComicAction(context, () => controller.apply(call), done: done);

    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Text('Phiên bản ảnh tham chiếu', style: Theme.of(context).textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700)),
      const SizedBox(height: 8),
      refs.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => Text(errorMessage(error)),
        data: (list) => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          if (list.variants.isEmpty)
            const Text('Chưa có ảnh tham chiếu. Render hoặc "Chỉ sinh keyframe" với Gemini, hoặc tải ảnh lên.'),
          if (list.variants.isNotEmpty)
            SizedBox(
              height: 190,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: list.variants.length,
                separatorBuilder: (_, _) => const SizedBox(width: 8),
                itemBuilder: (context, index) {
                  final v = list.variants[index];
                  return SizedBox(
                    width: 120,
                    child: Column(children: [
                      Expanded(
                        child: ClipRRect(
                          borderRadius: BorderRadius.circular(8),
                          child: DecoratedBox(
                            position: DecorationPosition.foreground,
                            decoration: BoxDecoration(
                              border: v.approved
                                  ? Border.all(color: AppColors.primary, width: 3)
                                  : Border.all(color: AppColors.border),
                              borderRadius: BorderRadius.circular(8),
                            ),
                            child: AuthImage(v.url),
                          ),
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'v${v.version}${v.uploaded ? ' · tải lên' : ''}${v.approved ? ' · Đã duyệt' : ''}',
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                      if (!v.approved && canEdit)
                        TextButton(
                          onPressed: () => edit(
                            (page) => repo.approveCharacterReference(jobId, characterId, v.name, page),
                            'Đã duyệt ảnh tham chiếu',
                          ),
                          child: const Text('Duyệt bản này'),
                        ),
                    ]),
                  );
                },
              ),
            ),
          if (canEdit)
            Wrap(spacing: 8, children: [
              OutlinedButton.icon(
                icon: const Icon(Icons.upload_outlined),
                label: const Text('Tải ảnh tham chiếu'),
                onPressed: () async {
                  final picture = await pickPicture();
                  if (picture == null) return;
                  await edit(
                    (page) => repo.uploadCharacterReference(jobId, characterId, picture, page),
                    'Đã tải và duyệt ảnh tham chiếu',
                  );
                },
              ),
              if (list.approved != null)
                OutlinedButton(
                  onPressed: () => edit(
                    (page) => repo.unpinCharacterReference(jobId, characterId, page),
                    'Đã bỏ ghim ảnh tham chiếu',
                  ),
                  child: const Text('Bỏ ghim (dùng theo mô tả)'),
                ),
            ]),
          const SizedBox(height: 4),
          Text(
            'Bản đã duyệt được dùng nguyên vẹn cho mọi keyframe; sửa mô tả chỉ tạo phiên bản mới, '
            'không ghi đè bản đã duyệt.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ]),
      ),
    ]);
  }
}
