import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:uuid/uuid.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../data/comic_repository.dart';
import '../../domain/comic_labels.dart';
import '../../domain/comic_models.dart';
import '../../domain/comic_options.dart';
import '../providers/comic_providers.dart';
import '../widgets/comic_media.dart';
import '../widgets/look_sheets.dart';

/// Payload of "Lưu cảnh" (web editor submit): the scene text, cast, place and prompts.
Map<String, dynamic> scenePatch({
  required String narration,
  required String dialogue,
  required String characters,
  required String visualPrompt,
  required String negativePrompt,
  required String cameraMotion,
  String? locationId,
}) =>
    {
      'narration': narration,
      'dialogue': [for (final line in parseDialogue(dialogue)) line.toJson()],
      'characters': splitIds(characters),
      'visual_prompt': visualPrompt,
      'negative_prompt': negativePrompt,
      'camera_motion': cameraMotion,
      'location_id': ?locationId,
    };

/// Payload of a shot's "Lưu" (shotPatchFromForm in animation-editor.js).
Map<String, dynamic> shotPatch({
  required String narration,
  required String dialogue,
  required String motionPrompt,
  required String keyframePrompt,
  required String negativePrompt,
  required String characters,
  required double durationSeconds,
  required String shotType,
  required String movement,
  required String angle,
  required String mode,
  required double motionStrength,
}) =>
    {
      'narration': narration,
      'dialogue': [for (final line in parseDialogue(dialogue)) line.toJson()],
      'motion_prompt': motionPrompt,
      'keyframe_prompt': keyframePrompt,
      'negative_prompt': negativePrompt,
      'characters': splitIds(characters),
      'duration_seconds': durationSeconds,
      'camera': {'shot_type': shotType, 'movement': movement, 'angle': angle},
      'animation': {'mode': mode, 'motion_strength': motionStrength},
    };

class SceneEditorScreen extends ConsumerWidget {
  const SceneEditorScreen({super.key, required this.jobId, required this.sceneIndex});

  final String jobId;
  final int sceneIndex;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final project = ref.watch(comicProjectProvider(jobId));
    final options = ref.watch(comicOptionsProvider).valueOrNull ?? const ComicOptions();
    return project.when(
      loading: () => Scaffold(
        appBar: StudioSubBar(title: 'Cảnh $sceneIndex'),
        body: const Center(child: CircularProgressIndicator()),
      ),
      error: (error, _) => Scaffold(
        appBar: StudioSubBar(title: 'Cảnh $sceneIndex'),
        body: ErrorView(message: errorMessage(error), onRetry: () => ref.invalidate(comicProjectProvider(jobId))),
      ),
      data: (project) {
        final scene = project.storyboard?.scenes.where((s) => s.sceneIndex == sceneIndex).firstOrNull;
        if (scene == null) {
          return Scaffold(
            appBar: StudioSubBar(title: 'Cảnh $sceneIndex'),
            body: const Center(child: Text('Cảnh này không còn trong trang hiện tại.')),
          );
        }
        return _SceneEditor(
          // A new version from the server rebuilds the form with its values.
          key: ValueKey('${scene.sceneIndex}-${scene.version}-${project.storyboard!.revision}'),
          project: project,
          scene: scene,
          options: options,
        );
      },
    );
  }
}

class _SceneEditor extends ConsumerStatefulWidget {
  const _SceneEditor({super.key, required this.project, required this.scene, required this.options});

  final ComicProject project;
  final ComicScene scene;
  final ComicOptions options;

  @override
  ConsumerState<_SceneEditor> createState() => _SceneEditorState();
}

class _SceneEditorState extends ConsumerState<_SceneEditor> {
  late final ComicScene scene = widget.scene;
  late final _narration = TextEditingController(text: scene.narration);
  late final _dialogue = TextEditingController(text: dialogueText(scene.dialogue));
  late final _characters = TextEditingController(text: scene.characters.join(', '));
  late final _visual = TextEditingController(text: scene.visualPrompt);
  late final _negative = TextEditingController(text: scene.negativePrompt);
  late String _cameraMotion = scene.cameraMotion;
  late String _locationId = scene.locationId;

  ComicProject get project => widget.project;
  Storyboard get storyboard => project.storyboard!;
  String get jobId => project.job.id;
  ComicRepository get _repo => ref.read(comicRepositoryProvider);
  ComicProjectController get _controller => ref.read(comicProjectProvider(jobId).notifier);

  @override
  void dispose() {
    for (final controller in [_narration, _dialogue, _characters, _visual, _negative]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<bool> _edit(Future<ComicProject> Function(ScenePage page) edit, String done) =>
      runComicAction(context, () => _controller.apply(edit), done: done);

  Future<void> _save() async {
    final Map<String, dynamic> patch;
    try {
      patch = scenePatch(
        narration: _narration.text,
        dialogue: _dialogue.text,
        characters: _characters.text,
        visualPrompt: _visual.text,
        negativePrompt: _negative.text,
        cameraMotion: _cameraMotion,
        locationId: storyboard.locations.isEmpty ? null : _locationId,
      );
    } on FormatException catch (error) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error.message)));
      return;
    }
    await _edit((page) => _repo.patchScene(jobId, scene.sceneIndex, patch, page), 'Đã lưu cảnh ${scene.sceneIndex}');
  }

  Future<void> _fix(Map<String, dynamic> fix) async {
    // The edit bumps the scene version, which rebuilds this screen: keep the controller.
    final controller = _controller;
    final ok = await _edit(
      (page) => _repo.fixScene(jobId, scene.sceneIndex, fix, page),
      fix['draw_now'] == false ? 'Đã sửa cảnh ${scene.sceneIndex}' : 'Đang vẽ lại cảnh ${scene.sceneIndex}…',
    );
    if (ok) await controller.reload();
  }

  Future<void> _insertScene() async {
    final values = await _askTexts(context, 'Chèn cảnh sau cảnh ${scene.sceneIndex}', [
      ('Lời dẫn cho cảnh mới', ''),
      ('Mô tả hình ảnh (tiếng Anh, để trống: dùng lời dẫn)', ''),
    ]);
    if (values == null || values[0].trim().isEmpty) return;
    final visual = values[1].trim().isEmpty ? values[0] : values[1];
    await _edit(
      (page) => _repo.insertScene(jobId, after: scene.sceneIndex, narration: values[0], visualPrompt: visual, page: page),
      'Đã chèn cảnh',
    );
  }

  Future<void> _deleteScene() async {
    if (!await confirmComic(context, 'Xóa cảnh ${scene.sceneIndex}?', action: 'Xóa') || !mounted) return;
    final router = GoRouter.of(context);
    final ok = await _edit((page) => _repo.deleteScene(jobId, scene.sceneIndex, page), 'Đã xóa cảnh');
    if (ok) router.pop();
  }

  @override
  Widget build(BuildContext context) {
    final canEdit = !comicReadOnly(ref) && !project.locked;
    return Scaffold(
      appBar: StudioSubBar(
        title: 'Cảnh ${scene.sceneIndex}',
        subtitle: storyboard.animated
            ? '${scene.shots.length} shot hoạt hình'
            : 'Phiên bản v${scene.version}',
        actions: [
          if (canEdit)
            PopupMenuButton<String>(
              onSelected: (value) => value == 'insert' ? _insertScene() : _deleteScene(),
              itemBuilder: (_) => [
                if (!storyboard.animated) const PopupMenuItem(value: 'insert', child: Text('+ Cảnh sau')),
                const PopupMenuItem(value: 'delete', child: Text('Xóa cảnh')),
              ],
            ),
        ],
      ),
      body: ListView(
        padding: EdgeInsets.fromLTRB(
          16,
          16,
          16,
          studioDockBottomPadding(context),
        ),
        children: [
          if (project.locked)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: StudioCard(
                color: AppColors.warningBg,
                borderColor: AppColors.warningBorder,
                child: const Text(
                  'Dự án đang được xử lý; hãy chờ xong hoặc dừng trước khi sửa.',
                  style: TextStyle(color: AppColors.warning, fontWeight: FontWeight.w600),
                ),
              ),
            ),
          if (storyboard.animated)
            ..._shots(canEdit)
          else ...[
            _image(canEdit),
            const SizedBox(height: 16),
            ..._form(canEdit),
          ],
        ],
      ),
    );
  }

  // ----------------------------------------------------------- comic scene
  Widget _image(bool canEdit) {
    final image = project.media.sceneImage(scene.sceneIndex);
    final ratio = (project.settings['aspect_ratio'] ?? '9:16') == '16:9' ? 16 / 9 : 9 / 16;
    return StudioCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 420),
              child: AspectRatio(
                aspectRatio: ratio,
                child: Container(
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: AppColors.border),
                    color: AppColors.surfaceMuted,
                  ),
                  clipBehavior: Clip.antiAlias,
                  child: Stack(fit: StackFit.expand, children: [
                    if (image != null)
                      AuthImage(image, fit: BoxFit.contain)
                    else
                      const Center(
                        child: Padding(
                          padding: EdgeInsets.all(16),
                          child: Text(
                            'Chưa có ảnh — bấm "Chỉ sinh ảnh" hoặc "Render video"',
                            textAlign: TextAlign.center,
                            style: TextStyle(color: AppColors.textSecondary),
                          ),
                        ),
                      ),
                    if (scene.imageOverride != null)
                      const Positioned(
                        top: 8,
                        left: 8,
                        child: Chip(label: Text('Ảnh tải lên'), visualDensity: VisualDensity.compact),
                      ),
                  ]),
                ),
              ),
            ),
          ),
          const SizedBox(height: 10),
          if (scene.imageOverride == null && canEdit)
            Wrap(spacing: 8, runSpacing: 6, children: [
              ActionChip(label: const Text('↻ Vẽ lại'), onPressed: () => _fix({'kind': 'redraw'})),
              ActionChip(label: const Text('Sai nhân vật'), onPressed: () => _openFix('characters')),
              if (storyboard.locations.isNotEmpty)
                ActionChip(label: const Text('Sai bối cảnh'), onPressed: () => _openFix('location')),
              if (storyboard.characters.isNotEmpty)
                ActionChip(label: const Text('Sai trang phục'), onPressed: () => _openFix('outfit')),
            ]),
          if (scene.fixNoteVi.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(
                'Đã ghi chú sửa: ${scene.fixNoteVi}',
                style: Theme.of(context).textTheme.bodySmall?.copyWith(color: AppColors.primary),
              ),
            ),
          if (canEdit) ...[
            const SizedBox(height: 8),
            Wrap(spacing: 8, children: [
              OutlinedButton.icon(
                icon: const Icon(Icons.image_outlined),
                label: const Text('Đổi ảnh'),
                onPressed: () async {
                  final picture = await pickPicture();
                  if (picture == null) return;
                  await _edit(
                    (page) => _repo.uploadSceneImage(jobId, scene.sceneIndex, picture, page),
                    'Đã thay ảnh cảnh ${scene.sceneIndex}',
                  );
                },
              ),
              if (scene.imageOverride != null)
                OutlinedButton(
                  onPressed: () =>
                      _edit((page) => _repo.clearSceneImage(jobId, scene.sceneIndex, page), 'Đã bỏ ảnh tải lên'),
                  child: const Text('Bỏ ảnh tải lên'),
                ),
            ]),
          ],
        ],
      ),
    );
  }

  void _openFix(String kind) {
    showLookSheet(
      context,
      builder: (sheetContext) => _FixSheet(
        kind: kind,
        scene: scene,
        storyboard: storyboard,
        onSubmit: (fix) async {
          Navigator.pop(sheetContext);
          await _fix(fix);
        },
      ),
    );
  }

  List<Widget> _form(bool canEdit) {
    final motions = widget.options.cameraMotions.isEmpty ? cameraMotionLabels.keys.toList() : widget.options.cameraMotions;
    final placeName = storyboard.location(scene.locationId)?.name ?? scene.location;
    InputDecoration deco(String label, {String? hint}) => InputDecoration(
          labelText: label,
          hintText: hint,
          alignLabelWithHint: true,
        );
    return [
      StudioCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              ['v${scene.version}', if (scene.emotion.isNotEmpty) scene.emotion, if (placeName.isNotEmpty) placeName]
                  .join(' · '),
              style: const TextStyle(color: AppColors.textSecondary, fontWeight: FontWeight.w600, fontSize: 13),
            ),
            const SizedBox(height: 12),
            ComicDropdown<String>(
              label: 'Chuyển động camera',
              value: _cameraMotion,
              enabled: canEdit,
              items: [for (final m in motions) (m, cameraMotionLabels[m] ?? m, true)],
              onChanged: (v) => _cameraMotion = v,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _narration,
              enabled: canEdit,
              minLines: 3,
              maxLines: 8,
              maxLength: 1500,
              decoration: deco('Lời dẫn chuyện'),
            ),
            const SizedBox(height: 10),
            _DialogueListEditor(
              controller: _dialogue,
              characters: storyboard.characters,
              enabled: canEdit,
            ),
            const SizedBox(height: 12),
            _CharacterChipsEditor(
              controller: _characters,
              characters: storyboard.characters,
              enabled: canEdit,
            ),
            const SizedBox(height: 12),
            if (storyboard.locations.isNotEmpty) ...[
              ComicDropdown<String>(
                label: 'Bối cảnh',
                value: _locationId,
                enabled: canEdit,
                items: [
                  ('', '— Không gắn (dùng mô tả của cảnh)', true),
                  for (final place in storyboard.locations) (place.id, place.name, true),
                ],
                onChanged: (v) => _locationId = v,
              ),
              const SizedBox(height: 12),
            ],
            ExpansionTile(
              tilePadding: EdgeInsets.zero,
              title: const Text('Prompt hình ảnh'),
              children: [
                TextField(
                  controller: _visual,
                  enabled: canEdit,
                  minLines: 3,
                  maxLines: 8,
                  maxLength: 1500,
                  decoration: deco('Mô tả khung hình (tiếng Anh)'),
                ),
                const SizedBox(height: 10),
                TextField(
                  controller: _negative,
                  enabled: canEdit,
                  maxLength: 600,
                  decoration: deco('Negative prompt'),
                ),
              ],
            ),
            if (canEdit) ...[
              const SizedBox(height: 12),
              FilledButton(onPressed: _save, child: const Text('Lưu cảnh')),
            ],
          ],
        ),
      ),
    ];
  }

  // ---------------------------------------------------------- animated shots
  List<Widget> _shots(bool canEdit) {
    final media = project.media;
    return [
      Text([
        '${scene.shots.length} shot',
        if (scene.location.isNotEmpty) scene.location,
        if (scene.emotion.isNotEmpty) scene.emotion,
      ].join(' · ')),
      const SizedBox(height: 8),
      for (final shot in scene.shots)
        _ShotCard(
          label: '${scene.sceneIndex}.${shot.shotIndex}',
          shot: shot,
          still: media.shotStill('${scene.sceneIndex}.${shot.shotIndex}'),
          clip: media.shotClip('${scene.sceneIndex}.${shot.shotIndex}'),
          report: project.shotReports['${scene.sceneIndex}.${shot.shotIndex}'],
          onTap: () => showLookSheet(
            context,
            builder: (_) =>
                _ShotSheet(project: project, scene: scene, shot: shot, options: widget.options, canEdit: canEdit),
          ),
        ),
    ];
  }
}

/// Quick-fix panel (web fixPanel): who is in the scene, where it happens, or
/// the right outfit, plus a note for the image model; then redraw the scene.
class _FixSheet extends StatefulWidget {
  const _FixSheet({required this.kind, required this.scene, required this.storyboard, required this.onSubmit});

  final String kind;
  final ComicScene scene;
  final Storyboard storyboard;
  final ValueChanged<Map<String, dynamic>> onSubmit;

  @override
  State<_FixSheet> createState() => _FixSheetState();
}

class _FixSheetState extends State<_FixSheet> {
  late final Set<String> _cast = widget.scene.characters.toSet();
  late String _locationId = widget.scene.locationId;
  // Characters in the scene first.
  late final List<ComicCharacter> _ordered = [...widget.storyboard.characters]
    ..sort((a, b) =>
        (widget.scene.characters.contains(b.id) ? 1 : 0) - (widget.scene.characters.contains(a.id) ? 1 : 0));
  late String? _characterId = _ordered.firstOrNull?.id;
  late final _outfit = TextEditingController(text: _ordered.firstOrNull?.outfitVi ?? '');
  late final _note = TextEditingController(text: widget.scene.fixNoteVi);
  bool _applyToAll = false;

  @override
  void dispose() {
    _outfit.dispose();
    _note.dispose();
    super.dispose();
  }

  void _submit() {
    final fix = <String, dynamic>{'kind': widget.kind, 'note_vi': _note.text};
    switch (widget.kind) {
      case 'characters':
        fix['characters'] = [for (final c in widget.storyboard.characters) if (_cast.contains(c.id)) c.id];
      case 'location':
        fix['location_id'] = _locationId;
      case 'outfit':
        fix.addAll({'character_id': _characterId, 'outfit_vi': _outfit.text, 'apply_to_all': _applyToAll});
    }
    widget.onSubmit(fix);
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      shrinkWrap: true,
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      children: [
        if (widget.kind == 'characters') ...[
          Text('Ai có mặt trong cảnh này?', style: Theme.of(context).textTheme.titleMedium),
          for (final c in widget.storyboard.characters)
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              value: _cast.contains(c.id),
              title: Text(c.name),
              onChanged: (v) => setState(() => v == true ? _cast.add(c.id) : _cast.remove(c.id)),
            ),
          Text(
            'Nếu đúng người nhưng vẽ sai mặt, hãy ghim ảnh tham chiếu ở tab Nhân vật.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
        if (widget.kind == 'location')
          ComicDropdown<String>(
            label: 'Cảnh này diễn ra ở đâu?',
            value: _locationId,
            items: [
              ('', '— Không gắn bối cảnh', true),
              for (final place in widget.storyboard.locations) (place.id, place.name, true),
            ],
            onChanged: (v) => setState(() => _locationId = v),
          ),
        if (widget.kind == 'outfit') ...[
          ComicDropdown<String>(
            label: 'Nhân vật',
            value: _characterId ?? '',
            items: [for (final c in _ordered) (c.id, c.name, true)],
            onChanged: (v) => setState(() {
              _characterId = v;
              _outfit.text = widget.storyboard.character(v)?.outfitVi ?? '';
            }),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _outfit,
            maxLength: 400,
            decoration: const InputDecoration(
              labelText: 'Trang phục đúng',
              hintText: 'Ví dụ: áo giáp bạc, áo choàng đỏ',
            ),
          ),
          CheckboxListTile(
            contentPadding: EdgeInsets.zero,
            value: _applyToAll,
            title: const Text('Sửa cho nhân vật này ở mọi cảnh (không chỉ cảnh này)'),
            onChanged: (v) => setState(() => _applyToAll = v ?? false),
          ),
        ],
        const SizedBox(height: 12),
        TextField(
          controller: _note,
          maxLength: 400,
          minLines: 2,
          maxLines: 4,
          decoration: const InputDecoration(
            labelText: 'Ghi chú thêm cho AI vẽ (tùy chọn)',
            hintText: 'Ví dụ: trời đang mưa, Minh quay lưng lại',
            alignLabelWithHint: true,
          ),
        ),
        const SizedBox(height: 12),
        FilledButton(onPressed: _submit, child: const Text('Sửa & vẽ lại cảnh này')),
      ],
    );
  }
}

class _ShotCard extends StatelessWidget {
  const _ShotCard({
    required this.label,
    required this.shot,
    required this.still,
    required this.clip,
    required this.report,
    required this.onTap,
  });

  final String label;
  final Shot shot;
  final String? still;
  final String? clip;
  final Map<String, dynamic>? report;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: StudioCard(
        padding: const EdgeInsets.all(12),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(10),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Container(
              width: 96,
              height: 96,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: AppColors.border),
                color: AppColors.surfaceMuted,
              ),
              clipBehavior: Clip.antiAlias,
              child: Stack(fit: StackFit.expand, children: [
                if (still != null) AuthImage(still!),
                if (clip != null)
                  Center(
                    child: IconButton.filledTonal(
                      icon: const Icon(Icons.play_arrow),
                      onPressed: () => showComicClip(context, clip!),
                    ),
                  ),
              ]),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('Shot $label', style: Theme.of(context).textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800)),
                const SizedBox(height: 4),
                Wrap(spacing: 4, runSpacing: 4, children: [
                  for (final chip in shotStatusChips(report, shot))
                    Chip(label: Text(chip), visualDensity: VisualDensity.compact, padding: EdgeInsets.zero),
                ]),
                const SizedBox(height: 4),
                Text(
                  shot.narration.isNotEmpty ? shot.narration : shot.keyframePrompt,
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                ),
              ]),
            ),
          ]),
        ),
      ),
    );
  }
}

/// Generation report badges of a shot (statusBadges in animation-editor.js).
List<String> shotStatusChips(Map<String, dynamic>? report, Shot shot) {
  if (report == null) return const ['Chưa tạo'];
  final provider = report['provider']?.toString();
  final qc = report['qc'];
  final attempts = report['attempts'];
  final duration = report['duration'];
  return [
    if (report['fallback'] == true)
      'Dự phòng 2.5D'
    else if (provider != null)
      provider == 'camera_2_5d' ? 'Camera 2.5D' : 'AI · ${report['model'] ?? provider}',
    if (qc is Map && qc['passed'] == false) 'QC chưa đạt',
    if (attempts is num && attempts > 1) '$attempts lần thử',
    if (report['billed_seconds'] != null && report['billed_seconds'] != 0) 'AI ${report['billed_seconds']}s',
    if (report['too_long'] == true) 'Shot quá dài',
    if (duration is num && duration > 0) '${duration.toStringAsFixed(1)}s',
    'take ${shot.take}',
  ];
}

/// One shot of an animated storyboard: keyframe, motion, camera and voice.
class _ShotSheet extends ConsumerStatefulWidget {
  const _ShotSheet({
    required this.project,
    required this.scene,
    required this.shot,
    required this.options,
    required this.canEdit,
  });

  final ComicProject project;
  final ComicScene scene;
  final Shot shot;
  final ComicOptions options;
  final bool canEdit;

  @override
  ConsumerState<_ShotSheet> createState() => _ShotSheetState();
}

class _ShotSheetState extends ConsumerState<_ShotSheet> {
  Shot get shot => widget.shot;
  late final _narration = TextEditingController(text: shot.narration);
  late final _dialogue = TextEditingController(text: dialogueText(shot.dialogue));
  late final _motion = TextEditingController(text: shot.motionPrompt);
  late final _keyframe = TextEditingController(text: shot.keyframePrompt);
  late final _characters = TextEditingController(text: shot.characters.join(', '));
  late final _duration = TextEditingController(text: '${shot.durationSeconds}');
  late final _strength = TextEditingController(text: '${shot.motionStrength}');
  late final _negative = TextEditingController(text: shot.negativePrompt);
  late String _shotType = shot.shotType;
  late String _movement = shot.movement;
  late String _angle = shot.angle;
  late String _mode = shot.animationMode;

  String get jobId => widget.project.job.id;
  int get sceneIndex => widget.scene.sceneIndex;
  String get label => '$sceneIndex.${shot.shotIndex}';
  ComicRepository get _repo => ref.read(comicRepositoryProvider);

  @override
  void dispose() {
    for (final c in [_narration, _dialogue, _motion, _keyframe, _characters, _duration, _strength, _negative]) {
      c.dispose();
    }
    super.dispose();
  }

  /// Runs an edit and closes the sheet on success (the list shows the result).
  Future<void> _edit(Future<ComicProject> Function(ScenePage page) edit, String done) async {
    final ok = await runComicAction(
      context,
      () => ref.read(comicProjectProvider(jobId).notifier).apply(edit),
      done: done,
    );
    if (ok && mounted) Navigator.pop(context);
  }

  Future<void> _save() async {
    final Map<String, dynamic> patch;
    try {
      patch = shotPatch(
        narration: _narration.text,
        dialogue: _dialogue.text,
        motionPrompt: _motion.text,
        keyframePrompt: _keyframe.text,
        negativePrompt: _negative.text,
        characters: _characters.text,
        durationSeconds: double.tryParse(_duration.text.replaceAll(',', '.')) ?? shot.durationSeconds,
        shotType: _shotType,
        movement: _movement,
        angle: _angle,
        mode: _mode,
        motionStrength: double.tryParse(_strength.text.replaceAll(',', '.')) ?? shot.motionStrength,
      );
    } on FormatException catch (error) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error.message)));
      return;
    }
    await _edit((page) => _repo.patchShot(jobId, sceneIndex, shot.shotIndex, patch, page), 'Đã lưu shot $label');
  }

  Future<void> _preview() async {
    // One key per tap; the revision makes a later preview of an edited shot a new request.
    final key = '$label-${widget.project.storyboard?.revision}-${const Uuid().v4()}';
    final ok = await runComicAction(context, () async {
      await _repo.render(jobId, RenderRequest.shot(label), idempotencyKey: key);
      await ref.read(comicProjectProvider(jobId).notifier).reload();
    }, done: 'Đang dựng xem trước shot $label…');
    if (ok && mounted) Navigator.pop(context);
  }

  Future<void> _insert() async {
    final values = await _askTexts(context, 'Chèn shot sau shot $label', [
      ('Mô tả khung hình đầu của shot mới (tiếng Anh)', ''),
      ('Lời dẫn (có thể để trống)', ''),
    ]);
    if (values == null || values[0].trim().isEmpty) return;
    await _edit(
      (page) => _repo.insertShot(
        jobId,
        sceneIndex,
        after: shot.shotIndex,
        keyframePrompt: values[0],
        narration: values[1],
        page: page,
      ),
      'Đã chèn shot',
    );
  }

  @override
  Widget build(BuildContext context) {
    final canEdit = widget.canEdit;
    final options = widget.options;
    InputDecoration deco(String label) => InputDecoration(labelText: label, alignLabelWithHint: true);
    List<(String, String, bool)> choices(List<String> values, Map<String, String> labels, String current) =>
        [for (final v in values.isEmpty ? [current] : values) (v, labels[v] ?? v, true)];
    const gap = SizedBox(height: 12);

    return ListView(
      shrinkWrap: true,
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      children: [
        Row(children: [
          Expanded(child: Text('Shot $label', style: Theme.of(context).textTheme.titleLarge)),
          if (shot.keyframeOverride != null) const Chip(label: Text('Keyframe tải lên')),
        ]),
        gap,
        ComicDropdown<String>(
          label: 'Kiểu chuyển động',
          value: _mode,
          enabled: canEdit,
          items: [for (final e in animationModeLabels.entries) (e.key, e.value, true)],
          onChanged: (v) => _mode = v,
        ),
        gap,
        ComicDropdown<String>(
          label: 'Cỡ cảnh',
          value: _shotType,
          enabled: canEdit,
          items: choices(options.shotTypes, shotTypeLabels, _shotType),
          onChanged: (v) => _shotType = v,
        ),
        gap,
        ComicDropdown<String>(
          label: 'Chuyển động máy',
          value: _movement,
          enabled: canEdit,
          items: choices(options.shotMovements, shotMovementLabels, _movement),
          onChanged: (v) => _movement = v,
        ),
        gap,
        ComicDropdown<String>(
          label: 'Góc máy',
          value: _angle,
          enabled: canEdit,
          items: choices(options.shotAngles, shotAngleLabels, _angle),
          onChanged: (v) => _angle = v,
        ),
        gap,
        TextField(
          controller: _narration,
          enabled: canEdit,
          minLines: 2,
          maxLines: 5,
          maxLength: 800,
          decoration: deco('Lời dẫn'),
        ),
        gap,
        _DialogueListEditor(
          controller: _dialogue,
          characters: widget.project.storyboard?.characters ?? const [],
          enabled: canEdit,
        ),
        gap,
        TextField(
          controller: _motion,
          enabled: canEdit,
          minLines: 2,
          maxLines: 5,
          maxLength: 800,
          decoration: deco('Chuyển động (tiếng Anh)'),
        ),
        ExpansionTile(
          tilePadding: EdgeInsets.zero,
          title: const Text('Keyframe, nhân vật, thời lượng'),
          children: [
            TextField(
              controller: _keyframe,
              enabled: canEdit,
              minLines: 3,
              maxLines: 6,
              maxLength: 1200,
              decoration: deco('Khung hình đầu (tiếng Anh)'),
            ),
            gap,
            _CharacterChipsEditor(
              controller: _characters,
              characters: widget.project.storyboard?.characters ?? const [],
              enabled: canEdit,
            ),
            gap,
            Row(children: [
              Expanded(
                child: TextField(
                  controller: _duration,
                  enabled: canEdit,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  decoration: deco('Thời lượng tối thiểu (s)'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: TextField(
                  controller: _strength,
                  enabled: canEdit,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  decoration: deco('Độ mạnh chuyển động'),
                ),
              ),
            ]),
            gap,
            TextField(controller: _negative, enabled: canEdit, maxLength: 600, decoration: deco('Negative prompt')),
          ],
        ),
        if (canEdit) ...[
          FilledButton(onPressed: _save, child: const Text('Lưu')),
          gap,
          Wrap(spacing: 8, runSpacing: 8, children: [
            OutlinedButton(onPressed: _preview, child: const Text('Xem trước shot')),
            OutlinedButton(
              onPressed: () => _edit(
                (page) => _repo.regenerateShot(jobId, sceneIndex, shot.shotIndex, page),
                'Shot $label: sẽ tạo chuyển động mới ở lần dựng tới',
              ),
              child: const Text('Tạo lại chuyển động'),
            ),
            OutlinedButton.icon(
              icon: const Icon(Icons.image_outlined),
              label: const Text('Đổi keyframe'),
              onPressed: () async {
                final picture = await pickPicture();
                if (picture == null) return;
                await _edit(
                  (page) => _repo.uploadKeyframe(jobId, sceneIndex, shot.shotIndex, picture, page),
                  'Đã thay keyframe',
                );
              },
            ),
            if (shot.keyframeOverride != null)
              OutlinedButton(
                onPressed: () => _edit(
                  (page) => _repo.clearKeyframe(jobId, sceneIndex, shot.shotIndex, page),
                  'Đã bỏ keyframe tải lên',
                ),
                child: const Text('Bỏ keyframe'),
              ),
            OutlinedButton(onPressed: _insert, child: const Text('+ Shot sau')),
            OutlinedButton(
              style: OutlinedButton.styleFrom(foregroundColor: Theme.of(context).colorScheme.error),
              onPressed: () async {
                if (!await confirmComic(context, 'Xóa shot $label?', action: 'Xóa')) return;
                await _edit((page) => _repo.deleteShot(jobId, sceneIndex, shot.shotIndex, page), 'Đã xóa shot');
              },
              child: const Text('Xóa shot'),
            ),
          ]),
        ],
      ],
    );
  }
}

/// A dialog with several text fields; null when cancelled.
Future<List<String>?> _askTexts(BuildContext context, String title, List<(String, String)> fields) {
  final controllers = [for (final field in fields) TextEditingController(text: field.$2)];
  return showDialog<List<String>>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(title),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (var i = 0; i < fields.length; i++) ...[
            if (i > 0) const SizedBox(height: 12),
            TextField(
              controller: controllers[i],
              decoration: InputDecoration(labelText: fields[i].$1),
            ),
          ],
        ],
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Hủy')),
        FilledButton(
          onPressed: () => Navigator.pop(context, [for (final c in controllers) c.text]),
          child: const Text('Thêm'),
        ),
      ],
    ),
  );
}

class _CharacterChipsEditor extends StatefulWidget {
  const _CharacterChipsEditor({
    required this.controller,
    required this.characters,
    required this.enabled,
  });

  final TextEditingController controller;
  final List<ComicCharacter> characters;
  final bool enabled;

  @override
  State<_CharacterChipsEditor> createState() => _CharacterChipsEditorState();
}

class _CharacterChipsEditorState extends State<_CharacterChipsEditor> {
  void _toggle(String id) {
    final ids = splitIds(widget.controller.text);
    if (ids.contains(id)) {
      ids.remove(id);
    } else {
      ids.add(id);
    }
    setState(() {
      widget.controller.text = ids.join(', ');
    });
  }

  @override
  Widget build(BuildContext context) {
    final selected = splitIds(widget.controller.text).toSet();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          'Nhân vật xuất hiện trong cảnh',
          style: TextStyle(
            fontSize: 12.5,
            fontWeight: FontWeight.w700,
            color: AppColors.inkSoft,
          ),
        ),
        if (widget.characters.isNotEmpty) ...[
          const SizedBox(height: 8),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final c in widget.characters)
                FilterChip(
                  label: Text('${c.name} (${c.id})'),
                  selected: selected.contains(c.id),
                  onSelected: widget.enabled ? (_) => _toggle(c.id) : null,
                  selectedColor: AppColors.brandSoft,
                  checkmarkColor: AppColors.brandDark,
                  labelStyle: TextStyle(
                    fontSize: 12,
                    fontWeight: selected.contains(c.id)
                        ? FontWeight.w800
                        : FontWeight.w600,
                    color: selected.contains(c.id)
                        ? AppColors.brandDark
                        : AppColors.ink,
                  ),
                ),
            ],
          ),
        ],
        const SizedBox(height: 8),
        TextField(
          controller: widget.controller,
          enabled: widget.enabled,
          onChanged: (_) => setState(() {}),
          decoration: InputDecoration(
            isDense: true,
            labelText: 'ID nhân vật (cách nhau dấu phẩy)',
            hintText: widget.characters.isNotEmpty
                ? widget.characters.map((c) => c.id).join(', ')
                : 'minh, lan',
          ),
        ),
      ],
    );
  }
}

class _DialogueRowDraft {
  _DialogueRowDraft({required this.character, required String text})
      : controller = TextEditingController(text: text);

  String character;
  final TextEditingController controller;

  void dispose() => controller.dispose();
}

class _DialogueListEditor extends StatefulWidget {
  const _DialogueListEditor({
    required this.controller,
    required this.characters,
    required this.enabled,
  });

  final TextEditingController controller;
  final List<ComicCharacter> characters;
  final bool enabled;

  @override
  State<_DialogueListEditor> createState() => _DialogueListEditorState();
}

class _DialogueListEditorState extends State<_DialogueListEditor> {
  final List<_DialogueRowDraft> _rows = [];

  @override
  void initState() {
    super.initState();
    _parseInitial();
  }

  void _parseInitial() {
    final raw = widget.controller.text.trim();
    if (raw.isEmpty) return;
    for (final line in raw.split('\n')) {
      final trimmed = line.trim();
      if (trimmed.isEmpty) continue;
      final idx = trimmed.indexOf(':');
      if (idx >= 1) {
        _rows.add(
          _DialogueRowDraft(
            character: trimmed.substring(0, idx).trim(),
            text: trimmed.substring(idx + 1).trim(),
          ),
        );
      } else {
        final fallbackChar = widget.characters.firstOrNull?.id ?? 'char_1';
        _rows.add(_DialogueRowDraft(character: fallbackChar, text: trimmed));
      }
    }
  }

  @override
  void dispose() {
    for (final r in _rows) {
      r.dispose();
    }
    super.dispose();
  }

  void _syncToController() {
    final lines = <String>[];
    for (final row in _rows) {
      final charId = row.character.trim().isEmpty
          ? (widget.characters.firstOrNull?.id ?? 'char_1')
          : row.character.trim();
      final text = row.controller.text.trim();
      if (text.isNotEmpty) {
        lines.add('$charId: $text');
      }
    }
    widget.controller.text = lines.join('\n');
  }

  void _addRow() {
    final defaultChar = widget.characters.firstOrNull?.id ?? 'char_1';
    setState(() {
      _rows.add(_DialogueRowDraft(character: defaultChar, text: ''));
    });
  }

  void _removeRow(int index) {
    setState(() {
      final removed = _rows.removeAt(index);
      removed.dispose();
      _syncToController();
    });
  }

  @override
  Widget build(BuildContext context) {
    final knownChars = {
      for (final c in widget.characters) c.id: '${c.name} (${c.id})',
    };

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surfaceSubtle,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Icon(
                Icons.chat_bubble_outline_rounded,
                size: 16,
                color: AppColors.brandDark,
              ),
              const SizedBox(width: 6),
              const Expanded(
                child: Text(
                  'Lời thoại theo nhân vật',
                  style: TextStyle(
                    fontSize: 12.8,
                    fontWeight: FontWeight.w800,
                    color: AppColors.ink,
                  ),
                ),
              ),
              if (widget.enabled)
                TextButton.icon(
                  style: TextButton.styleFrom(
                    visualDensity: VisualDensity.compact,
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                  ),
                  onPressed: _addRow,
                  icon: const Icon(Icons.add_rounded, size: 16),
                  label: const Text(
                    '+ Thêm lời thoại',
                    style: TextStyle(fontSize: 12, fontWeight: FontWeight.w800),
                  ),
                ),
            ],
          ),
          if (_rows.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 6),
              child: Text(
                'Chưa có lời thoại trong cảnh này. Bấm "+ Thêm lời thoại" để gán nhân vật nói.',
                style: TextStyle(fontSize: 12, color: AppColors.muted),
              ),
            )
          else
            for (var i = 0; i < _rows.length; i++) ...[
              const SizedBox(height: 8),
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: 132,
                    child: knownChars.isNotEmpty
                        ? DropdownButtonFormField<String>(
                            key: ValueKey('dlg_char_${i}_${_rows[i].character}'),
                            initialValue: knownChars.containsKey(_rows[i].character)
                                ? _rows[i].character
                                : null,
                            isExpanded: true,
                            decoration: const InputDecoration(
                              isDense: true,
                              labelText: 'Nhân vật',
                              contentPadding: EdgeInsets.symmetric(
                                horizontal: 8,
                                vertical: 9,
                              ),
                            ),
                            items: [
                              if (!knownChars.containsKey(_rows[i].character) &&
                                  _rows[i].character.isNotEmpty)
                                DropdownMenuItem(
                                  value: _rows[i].character,
                                  child: Text(
                                    _rows[i].character,
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(fontSize: 12),
                                  ),
                                ),
                              for (final entry in knownChars.entries)
                                DropdownMenuItem(
                                  value: entry.key,
                                  child: Text(
                                    entry.value,
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(fontSize: 12),
                                  ),
                                ),
                            ],
                            onChanged: widget.enabled
                                ? (v) {
                                    if (v == null) return;
                                    setState(() => _rows[i].character = v);
                                    _syncToController();
                                  }
                                : null,
                          )
                        : TextFormField(
                            initialValue: _rows[i].character,
                            enabled: widget.enabled,
                            decoration: const InputDecoration(
                              isDense: true,
                              labelText: 'ID nhân vật',
                            ),
                            onChanged: (v) {
                              _rows[i].character = v;
                              _syncToController();
                            },
                          ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: TextField(
                      controller: _rows[i].controller,
                      enabled: widget.enabled,
                      minLines: 1,
                      maxLines: 3,
                      onChanged: (_) => _syncToController(),
                      decoration: const InputDecoration(
                        isDense: true,
                        labelText: 'Câu thoại',
                        hintText: 'Nhập lời nhân vật nói…',
                      ),
                    ),
                  ),
                  if (widget.enabled)
                    IconButton(
                      visualDensity: VisualDensity.compact,
                      tooltip: 'Xóa dòng thoại',
                      icon: const Icon(
                        Icons.close_rounded,
                        size: 18,
                        color: AppColors.muted,
                      ),
                      onPressed: () => _removeRow(i),
                    ),
                ],
              ),
            ],
        ],
      ),
    );
  }
}
