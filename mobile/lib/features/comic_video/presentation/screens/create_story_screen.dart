import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:uuid/uuid.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../data/comic_repository.dart';
import '../../domain/comic_forms.dart';
import '../../domain/comic_options.dart';
import '../../domain/story_bible.dart';
import '../providers/comic_providers.dart';
import '../widgets/bible_editor.dart';
import '../widgets/comic_media.dart';
import '../widgets/form_fields.dart';

/// "Tạo truyện mới": step 1 the story and settings, step 2 review the
/// characters and places the AI proposes, then `POST /comic/series`.
class CreateStoryScreen extends ConsumerWidget {
  const CreateStoryScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final options = ref.watch(comicOptionsProvider);
    return options.when(
      loading: () => const Scaffold(
        appBar: StudioSubBar(title: 'Tạo truyện mới'),
        body: Center(child: CircularProgressIndicator()),
      ),
      error: (error, _) => Scaffold(
        appBar: const StudioSubBar(title: 'Tạo truyện mới'),
        body: ErrorView(message: errorMessage(error), onRetry: () => ref.invalidate(comicOptionsProvider)),
      ),
      data: (options) => _CreateStory(options: options),
    );
  }
}

class _CreateStory extends ConsumerStatefulWidget {
  const _CreateStory({required this.options});

  final ComicOptions options;

  @override
  ConsumerState<_CreateStory> createState() => _CreateStoryState();
}

class _CreateStoryState extends ConsumerState<_CreateStory> {
  late final StoryForm form = StoryForm.withOptions(widget.options);
  LocalFile? _file;
  LocalFile? _music;
  BibleDraft? _bible;
  bool _busy = false;
  double? _upload;

  /// One key per story: a retry after a network error cannot create it twice.
  String? _idempotencyKey;

  ComicOptions get options => widget.options;

  Future<void> _review() async {
    if (form.text.trim().isEmpty && _file == null && form.storyBrief.trim().isEmpty) {
      _snack('Hãy dán nội dung tập 1 (hoặc chọn file) để AI đề xuất nhân vật và bối cảnh');
      return;
    }
    setState(() => _busy = true);
    try {
      final bible = BibleDraft.fromJson(
          await ref.read(comicRepositoryProvider).suggestBible(form.suggestFields(), file: _file))
        ..clearStatus();
      setState(() => _bible = bible);
    } catch (error) {
      if (!mounted) return;
      final skip = await confirmComic(
        context,
        '${errorMessage(error)}\n\nTạo truyện luôn và để AI tự nhận ra nhân vật khi chia cảnh?',
        action: 'Tạo luôn',
      );
      if (skip) await _create(null);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _create(BibleDraft? bible) async {
    setState(() {
      _busy = true;
      _upload = null;
    });
    _idempotencyKey ??= const Uuid().v4();
    try {
      final created = await ref.read(comicRepositoryProvider).createSeries(
            form.toFields(bible: bible?.toJsonString()),
            file: _file,
            music: _music,
            idempotencyKey: _idempotencyKey,
            onProgress: (sent, total) {
              if (total > 0 && mounted) setState(() => _upload = sent / total);
            },
          );
      ref.invalidate(comicSeriesListProvider);
      ref.invalidate(comicProjectsProvider);
      _snack(created['job_id'] != null
          ? 'Đã tạo truyện và tập 1, hệ thống đang chia cảnh…'
          : 'Đã tạo truyện — hãy thêm tập 1');
      if (mounted) context.pushReplacement('/comic/series/${created['series_id']}');
    } catch (error) {
      _snack(errorMessage(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _snack(String message) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    final bible = _bible;
    return PopScope(
      canPop: bible == null,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) setState(() => _bible = null);
      },
      child: Scaffold(
        appBar: StudioSubBar(
          title: bible == null ? 'Tạo truyện mới' : 'Duyệt nhân vật & bối cảnh',
          subtitle: bible == null
              ? 'Thiết lập thế giới, phong cách & tập 1'
              : 'Bước 2 · Kiểm tra hồ sơ nhân vật AI đề xuất',
        ),
        body: Column(children: [
          if (_busy) LinearProgressIndicator(value: _upload),
          Expanded(child: bible == null ? _storyStep() : _reviewStep(bible)),
        ]),
        bottomNavigationBar: SafeArea(
          child: Container(
            padding: const EdgeInsets.all(12),
            decoration: const BoxDecoration(
              color: AppColors.surface,
              border: Border(top: BorderSide(color: AppColors.border)),
            ),
            child: bible == null
                ? Row(children: [
                    Expanded(
                      child: OutlinedButton(
                        onPressed: _busy ? null : () => _create(null),
                        child: const Text('Tạo luôn'),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      flex: 2,
                      child: FilledButton(
                        onPressed: _busy ? null : _review,
                        child: Text(_busy ? 'AI đang đọc truyện…' : 'AI đề xuất nhân vật'),
                      ),
                    ),
                  ])
                : Row(children: [
                    Expanded(
                      child: OutlinedButton(
                        onPressed: _busy ? null : () => setState(() => _bible = null),
                        child: const Text('Quay lại'),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      flex: 2,
                      child: FilledButton(
                        onPressed: _busy ? null : () => _create(bible),
                        child: const Text('Xác nhận & tạo truyện'),
                      ),
                    ),
                  ]),
          ),
        ),
      ),
    );
  }

  Widget _reviewStep(BibleDraft bible) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        StudioCard(
          color: AppColors.primarySoft.withValues(alpha: 0.45),
          borderColor: AppColors.primaryBorder,
          child: Text(
            'AI đề xuất ${bible.characters.length} nhân vật và ${bible.locations.length} bối cảnh. '
            'Sửa bằng tiếng Việt nếu cần — mọi cảnh sẽ được vẽ theo đúng hồ sơ này.',
          ),
        ),
        const SizedBox(height: 16),
        BibleEditor(draft: bible),
      ],
    );
  }

  List<(String, String, bool)> _choices(List<ProviderOption> items) =>
      [for (final item in items) (item.key, item.displayLabel, item.usable)];

  Widget _storyStep() {
    final cit = options.tts('cit');
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        StudioCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const StudioStepHeader(
                step: 1,
                title: 'Kịch bản & Thể loại',
                subtitle: 'Nhập tên truyện, nội dung tập 1 và chọn thể loại để AI thiết lập thế giới.',
              ),
              const SizedBox(height: 16),
              ComicTextField(
                label: 'Tên truyện',
                initial: form.title,
                maxLength: 200,
                onChanged: (v) => form.title = v,
              ),
              if (options.genres.isNotEmpty) ...[
                const ComicSection('Thể loại'),
                Wrap(
                  spacing: 8,
                  runSpacing: 6,
                  children: [
                    for (final genre in options.genres)
                      ChoiceChip(
                        label: Text(genre.label),
                        tooltip: genre.worldVi,
                        selected: form.genre == genre.key,
                        onSelected: (_) => setState(() => form.selectGenre(genre, options)),
                      ),
                  ],
                ),
                const SizedBox(height: 12),
              ],
              StatefulBuilder(
                builder: (context, setLocal) => Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: TextFormField(
                    initialValue: form.text,
                    minLines: 5,
                    maxLines: 12,
                    decoration: InputDecoration(
                      labelText: 'Nội dung tập 1 (có thể để trống, thêm sau)',
                      hintText: 'Đêm hôm đó, Minh bước vào khu rừng…',
                      helperText: '${form.text.length} ký tự'
                          '${options.maxStoryChars > 0 ? ' · tối đa ${options.maxStoryChars} ký tự mỗi dự án' : ''}',
                      alignLabelWithHint: true,
                    ),
                    onChanged: (v) => setLocal(() => form.text = v),
                  ),
                ),
              ),
              ComicFileField(
                label: 'Hoặc tải file truyện',
                file: _file,
                extensions: const ['txt', 'md', 'docx'],
                onChanged: (file) => setState(() => _file = file),
              ),
            ],
          ),
        ),
        const SizedBox(height: 14),
        StudioCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const StudioStepHeader(
                step: 2,
                title: 'Định dạng & Thông tin tập 1',
                subtitle: 'Chọn kiểu Truyện tranh hoặc Hoạt hình AI và tỷ lệ khung hình.',
              ),
              const SizedBox(height: 14),
              const ComicSection('Kiểu video'),
              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(
                    value: 'comic',
                    label: Text('Truyện tranh'),
                    icon: Icon(Icons.photo_library_outlined),
                  ),
                  ButtonSegment(
                    value: 'animated',
                    label: Text('Hoạt hình AI'),
                    icon: Icon(Icons.movie_outlined),
                  ),
                ],
                selected: {form.videoMode},
                onSelectionChanged: (value) => setState(() => form.videoMode = value.first),
              ),
              const SizedBox(height: 12),
              ComicDropdown<String>(
                label: 'Khung hình',
                value: form.aspectRatio,
                items: [for (final ratio in options.aspectRatios) (ratio, ratio, true)],
                onChanged: (v) => setState(() => form.aspectRatio = v),
              ),
              const SizedBox(height: 12),
              ComicTextField(
                label: 'Giới thiệu truyện (tùy chọn)',
                hint: 'Bối cảnh, thể loại, ghi chú cho cả bộ truyện',
                initial: form.description,
                maxLength: 10000,
                lines: 3,
                onChanged: (v) => form.description = v,
              ),
              ComicTextField(
                label: 'Ghi chú thêm cho AI (tùy chọn)',
                hint: 'Thế giới, ngoại hình từng nhân vật, các bối cảnh chính',
                initial: form.storyBrief,
                maxLength: 6000,
                lines: 5,
                onChanged: (v) => form.storyBrief = v,
              ),
              ComicTextField(
                label: 'Tên tập 1',
                hint: 'Để trống: Tập 1',
                initial: form.episodeTitle,
                maxLength: 200,
                onChanged: (v) => form.episodeTitle = v,
              ),
              ComicTextField(
                label: 'Tiêu đề video YouTube tập 1',
                hint: 'Để trống để AI đề xuất từ nội dung',
                initial: form.youtubeTitle,
                maxLength: 100,
                onChanged: (v) => form.youtubeTitle = v,
              ),
            ],
          ),
        ),
        const SizedBox(height: 14),
        StudioCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const StudioStepHeader(
                step: 3,
                title: 'AI Engine, Hình ảnh & Giọng đọc',
                subtitle: 'Cấu hình model sinh ảnh, chuyển động, giọng đọc TTS và phụ đề.',
              ),
              const SizedBox(height: 8),
              if (form.animated)
                ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  title: const Text('Hoạt hình AI', style: TextStyle(fontWeight: FontWeight.w700)),
                  initiallyExpanded: true,
                  childrenPadding: const EdgeInsets.only(bottom: 8),
                  children: [
                    ComicDropdown<String>(
                      label: 'AI tạo chuyển động',
                      value: form.animationProvider,
                      items: _choices(options.animationProviders),
                      onChanged: (v) => setState(() => form.animationProvider = v),
                    ),
                    const SizedBox(height: 12),
                    Row(children: [
                      Expanded(
                        child: ComicDropdown<String>(
                          label: 'Chất lượng',
                          value: form.quality,
                          items: const [('draft', 'Nháp', true), ('final', 'Hoàn thiện', true)],
                          onChanged: (v) => setState(() => form.quality = v),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: ComicDropdown<int>(
                          label: 'FPS',
                          value: form.fps,
                          items: const [(24, '24', true), (30, '30', true)],
                          onChanged: (v) => setState(() => form.fps = v),
                        ),
                      ),
                    ]),
                    const SizedBox(height: 12),
                    ComicNumberField(
                      label: 'Độ mạnh chuyển động (0–1)',
                      initial: form.motionStrength,
                      onChanged: (v) => form.motionStrength = v.toDouble(),
                    ),
                    ComicNumberField(
                      label: 'Số lần thử AI mỗi shot (1–4)',
                      integer: true,
                      initial: form.maxAnimationAttempts,
                      onChanged: (v) => form.maxAnimationAttempts = v.toInt(),
                    ),
                    ComicNumberField(
                      label: 'Ngân sách AI mỗi lần render (giây video)',
                      initial: form.animationBudgetSeconds,
                      onChanged: (v) => form.animationBudgetSeconds = v.toDouble(),
                    ),
                  ],
                ),
              ExpansionTile(
                tilePadding: EdgeInsets.zero,
                title: const Text('Hình ảnh & AI', style: TextStyle(fontWeight: FontWeight.w700)),
                childrenPadding: const EdgeInsets.only(bottom: 8),
                children: [
                  ComicDropdown<String>(
                    label: 'Phong cách hình ảnh',
                    value: form.style,
                    items: _choices(options.styles),
                    onChanged: (v) => setState(() => form.style = v),
                  ),
                  const SizedBox(height: 12),
                  ComicDropdown<String>(
                    label: 'AI chia cảnh',
                    value: form.llmProvider,
                    items: _choices(options.llmProviders),
                    onChanged: (v) => setState(() => form.llmProvider = v),
                  ),
                  const SizedBox(height: 12),
                  ComicDropdown<String>(
                    label: 'AI vẽ ảnh',
                    value: form.imageProvider,
                    items: _choices(options.imageProviders),
                    onChanged: (v) => setState(() => form.imageProvider = v),
                  ),
                  const SizedBox(height: 12),
                  ComicNumberField(
                    label: 'Seed ảnh (0 = ngẫu nhiên)',
                    integer: true,
                    initial: form.seed,
                    onChanged: (v) => form.seed = v.toInt(),
                  ),
                  ComicNumberField(
                    label: 'Thời lượng mỗi cảnh (giây)',
                    helper: 'Số cảnh: tự động theo nội dung truyện',
                    initial: form.targetSceneDuration,
                    onChanged: (v) => form.targetSceneDuration = v.toDouble(),
                  ),
                ],
              ),
              ExpansionTile(
                tilePadding: EdgeInsets.zero,
                title: const Text('Giọng đọc & âm thanh', style: TextStyle(fontWeight: FontWeight.w700)),
                childrenPadding: const EdgeInsets.only(bottom: 8),
                children: [
                  ComicDropdown<String>(
                    label: 'Ngôn ngữ lời đọc',
                    value: form.language,
                    items: _choices(options.languages),
                    onChanged: (v) => setState(() => form.language = v),
                  ),
                  const SizedBox(height: 12),
                  ComicDropdown<String>(
                    label: 'AI lồng tiếng',
                    value: form.ttsProvider,
                    items: [for (final p in options.ttsProviders) (p.key, p.displayLabel, p.usable)],
                    onChanged: (v) => setState(() {
                      form.ttsProvider = v;
                      form.syncCitVoices(options);
                    }),
                  ),
                  const SizedBox(height: 12),
                  ComicDropdown<String>(
                    label: 'Giọng người dẫn chuyện',
                    value: form.narratorVoice,
                    items: const [('female', 'Nữ', true), ('male', 'Nam', true)],
                    onChanged: (v) => setState(() {
                      form.narratorVoice = v;
                      form.syncCitVoices(options);
                    }),
                  ),
                  if (form.usesCit && cit != null) ...[
                    for (final (label, gender, value, set) in [
                      (
                        'Giọng CIT người dẫn chuyện',
                        form.narratorVoice,
                        form.citNarratorVoice,
                        (String v) => form.citNarratorVoice = v
                      ),
                      ('Giọng CIT nhân vật nữ', 'female', form.citFemaleVoice, (String v) => form.citFemaleVoice = v),
                      ('Giọng CIT nhân vật nam', 'male', form.citMaleVoice, (String v) => form.citMaleVoice = v),
                    ]) ...[
                      const SizedBox(height: 12),
                      ComicDropdown<String>(
                        key: ValueKey('$label-$gender'),
                        label: label,
                        value: value,
                        items: [for (final voice in cit.voicesFor(gender)) (voice.key, voice.label, true)],
                        onChanged: (v) => setState(() => set(v)),
                      ),
                    ],
                  ],
                  const SizedBox(height: 12),
                  ComicNumberField(
                    label: 'Tốc độ đọc (0.7–1.5)',
                    initial: form.speechRate,
                    onChanged: (v) => form.speechRate = v.toDouble(),
                  ),
                  ComicFileField(
                    label: 'Nhạc nền (tùy chọn)',
                    file: _music,
                    extensions: const ['mp3', 'm4a', 'aac', 'wav', 'ogg'],
                    onChanged: (file) => setState(() => _music = file),
                  ),
                  ComicNumberField(
                    label: 'Âm lượng nhạc nền (0–1)',
                    initial: form.musicVolume,
                    onChanged: (v) => form.musicVolume = v.toDouble(),
                  ),
                ],
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Chèn phụ đề vào video'),
                value: form.burnSubtitles,
                onChanged: (v) => setState(() => form.burnSubtitles = v),
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Tự render video sau khi chia cảnh'),
                subtitle: const Text('Tắt để duyệt storyboard trước khi render'),
                value: form.autoRender,
                onChanged: (v) => setState(() => form.autoRender = v),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
