import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../data/comic_repository.dart';
import '../../domain/comic_forms.dart';
import '../../domain/comic_models.dart';
import '../../domain/story_bible.dart';
import '../providers/comic_providers.dart';
import '../widgets/bible_editor.dart';
import '../widgets/form_fields.dart';

/// "Thêm tập N": the chapter is compared with the series' bible; only new
/// characters and places need review.
class CreateEpisodeScreen extends ConsumerWidget {
  const CreateEpisodeScreen({super.key, required this.seriesId});

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
      data: (series) => _CreateEpisode(series: series),
    );
  }
}

class _CreateEpisode extends ConsumerStatefulWidget {
  const _CreateEpisode({required this.series});

  final SeriesDetail series;

  @override
  ConsumerState<_CreateEpisode> createState() => _CreateEpisodeState();
}

class _CreateEpisodeState extends ConsumerState<_CreateEpisode> {
  late final EpisodeForm form = EpisodeForm(seriesId: widget.series.id);
  late final int number = widget.series.nextEpisodeNumber;
  LocalFile? _file;
  LocalFile? _music;
  BibleDraft? _bible;
  bool _busy = false;
  double? _upload;
  String? _idempotencyKey;

  void _snack(String message) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _submit() async {
    if (_file == null && form.text.trim().isEmpty) {
      _snack('Hãy dán nội dung tập hoặc chọn file TXT/DOCX');
      return;
    }
    setState(() => _busy = true);
    BibleDraft? bible;
    try {
      final settings = widget.series.settings;
      bible = BibleDraft.fromJson(await ref.read(comicRepositoryProvider).suggestBible(
            form.suggestFields(
              language: settings['language']?.toString() ?? 'vi',
              llmProvider: settings['llm_provider']?.toString() ?? 'auto',
            ),
            file: _file,
          ));
    } catch (error) {
      // No AI available: the scene split still adds new characters on its own.
      _snack('Bỏ qua bước duyệt: ${errorMessage(error)}');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
    final fresh = bible != null && [...bible.characters, ...bible.locations].any((item) => item['status'] == 'new');
    if (!fresh) {
      await _create(null);
      return;
    }
    bible.keepOnlyNew();
    setState(() => _bible = bible);
  }

  Future<void> _create(BibleDraft? bible) async {
    final approved = bible?.collect(onlyNew: true, withWorld: false);
    final hasNew = approved != null &&
        ((approved['characters'] as List).isNotEmpty || (approved['locations'] as List).isNotEmpty);
    setState(() {
      _busy = true;
      _upload = null;
    });
    _idempotencyKey ??= const Uuid().v4();
    try {
      final created = await ref.read(comicRepositoryProvider).createProject(
            form.toFields(bible: hasNew ? bible!.toJsonString(onlyNew: true, withWorld: false) : null),
            file: _file,
            music: _music,
            idempotencyKey: _idempotencyKey,
            onProgress: (sent, total) {
              if (total > 0 && mounted) setState(() => _upload = sent / total);
            },
          );
      _snack('Đã tạo tập ${created['episode_number'] ?? number}, hệ thống đang chia cảnh…');
      await ref.read(seriesDetailProvider(widget.series.id).notifier).reload();
      ref.invalidate(comicProjectsProvider);
      ref.invalidate(comicSeriesListProvider);
      if (mounted) Navigator.of(context).pop();
    } catch (error) {
      _snack(errorMessage(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
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
        appBar: AppBar(title: Text(bible == null ? 'Thêm tập $number' : 'Nhân vật & bối cảnh mới')),
        body: Column(children: [
          if (_busy) LinearProgressIndicator(value: _upload),
          Expanded(
            child: bible == null
                ? ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      StudioCard(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            const StudioEyebrow(label: 'THÊM TẬP TIẾP THEO'),
                            const SizedBox(height: 4),
                            Text(
                              widget.series.summary.title,
                              style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800),
                            ),
                            const SizedBox(height: 14),
                            ComicTextField(
                              label: 'Tên tập',
                              hint: 'Để trống: Tập $number',
                              initial: form.title,
                              maxLength: 200,
                              onChanged: (v) => form.title = v,
                            ),
                            ComicTextField(
                              label: 'Tiêu đề video YouTube',
                              hint: 'Để trống để AI đề xuất',
                              initial: form.youtubeTitle,
                              maxLength: 100,
                              onChanged: (v) => form.youtubeTitle = v,
                            ),
                            ComicTextField(
                              label: 'Nội dung tập',
                              hint: 'Ba ngày sau, Minh trở lại khu rừng…',
                              initial: form.text,
                              lines: 12,
                              onChanged: (v) => form.text = v,
                            ),
                            ComicFileField(
                              label: 'Hoặc tải file truyện',
                              file: _file,
                              extensions: const ['txt', 'md', 'docx'],
                              onChanged: (file) => setState(() => _file = file),
                            ),
                            ComicTextField(
                              label: 'Điểm mới của tập (tùy chọn)',
                              hint: 'Nơi diễn ra, nhân vật mới và ngoại hình, bối cảnh mới',
                              initial: form.episodeBrief,
                              maxLength: 3000,
                              lines: 4,
                              onChanged: (v) => form.episodeBrief = v,
                            ),
                            ComicFileField(
                              label: 'Nhạc nền (tùy chọn)',
                              file: _music,
                              extensions: const ['mp3', 'm4a', 'aac', 'wav', 'ogg'],
                              onChanged: (file) => setState(() => _music = file),
                            ),
                            SwitchListTile(
                              contentPadding: EdgeInsets.zero,
                              title: const Text('Tự render video sau khi chia cảnh'),
                              value: form.autoRender,
                              onChanged: (v) => setState(() => form.autoRender = v),
                            ),
                          ],
                        ),
                      ),
                    ],
                  )
                : ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      StudioCard(
                        color: AppColors.primarySoft.withValues(alpha: 0.45),
                        borderColor: AppColors.primaryBorder,
                        child: Text(
                          'Phát hiện ${bible.newCharacters} nhân vật mới và ${bible.newLocations} bối cảnh mới '
                          'trong tập này. Nhân vật và bối cảnh đã có của truyện được giữ nguyên.',
                        ),
                      ),
                      const SizedBox(height: 16),
                      BibleEditor(draft: bible, withWorld: false),
                    ],
                  ),
          ),
        ]),
        bottomNavigationBar: SafeArea(
          child: Container(
            padding: const EdgeInsets.all(12),
            decoration: const BoxDecoration(
              color: AppColors.surface,
              border: Border(top: BorderSide(color: AppColors.border)),
            ),
            child: bible == null
                ? FilledButton(
                    onPressed: _busy ? null : _submit,
                    child: Text(_busy ? 'AI đang tìm nhân vật & bối cảnh mới…' : 'Tạo tập $number'),
                  )
                : Row(children: [
                    Expanded(
                      child: OutlinedButton(
                        onPressed: _busy ? null : () => _create(null),
                        child: const Text('Bỏ qua'),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      flex: 2,
                      child: FilledButton(
                        onPressed: _busy ? null : () => _create(bible),
                        child: Text('Tạo tập $number'),
                      ),
                    ),
                  ]),
          ),
        ),
      ),
    );
  }
}
