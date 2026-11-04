import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/translation_repository.dart';
import '../../domain/options.dart';
import '../../domain/translation_config.dart';
import 'submission_queue.dart';

class TranslateFormState {
  const TranslateFormState({
    this.config = const TranslationConfig(),
    this.files = const [],
    this.linksText = '',
    this.music,
    this.error,
  });

  final TranslationConfig config;
  final List<PickedMedia> files;
  final String linksText;
  final PickedMedia? music;
  final String? error;

  List<String> get links => extractLinks(linksText);
  int get sourceCount => files.length + links.length;

  TranslateFormState copyWith({
    TranslationConfig? config,
    List<PickedMedia>? files,
    String? linksText,
    PickedMedia? music,
    bool clearMusic = false,
    String? error,
    bool clearError = false,
  }) => TranslateFormState(
    config: config ?? this.config,
    files: files ?? this.files,
    linksText: linksText ?? this.linksText,
    music: clearMusic ? null : (music ?? this.music),
    error: clearError ? null : (error ?? this.error),
  );
}

final translateFormProvider =
    NotifierProvider<TranslateFormController, TranslateFormState>(
      TranslateFormController.new,
    );

/// Form state survives tab switches (not auto-disposed) so a half-filled form
/// is kept while the user looks at other tabs.
class TranslateFormController extends Notifier<TranslateFormState> {
  TranslationRepository get _repo => ref.read(translationRepositoryProvider);

  @override
  TranslateFormState build() {
    unawaited(_restore());
    return const TranslateFormState();
  }

  Future<void> _restore() async {
    final config = await _repo.loadConfig();
    state = state.copyWith(
      config: config.copyWith(batchName: state.config.batchName),
    );
  }

  void updateConfig(TranslationConfig config) {
    state = state.copyWith(config: config, clearError: true);
  }

  void addFiles(List<PickedMedia> files) {
    final known = state.files.map((f) => f.path).toSet();
    state = state.copyWith(
      files: [...state.files, ...files.where((f) => known.add(f.path))],
      clearError: true,
    );
  }

  void removeFile(PickedMedia file) {
    state = state.copyWith(
      files: state.files.where((f) => f.path != file.path).toList(),
    );
  }

  void clearFiles() {
    state = state.copyWith(files: const [], clearError: true);
  }

  void setLinks(String text) {
    state = state.copyWith(linksText: text, clearError: true);
  }

  void clearLinks() {
    state = state.copyWith(linksText: '', clearError: true);
  }

  void removeLink(String url) {
    final remaining = state.links.where((l) => l != url).toList();
    state = state.copyWith(linksText: remaining.join('\n'), clearError: true);
  }

  /// Appends [raw] (e.g. from the system clipboard) and normalizes to clean
  /// line-separated social links when valid URLs are found. Returns how many
  /// new valid links were added.
  int appendLinksText(String raw) {
    final incoming = extractLinks(raw);
    if (incoming.isEmpty) return 0;
    final current = state.links;
    final known = current.toSet();
    final added = incoming.where(known.add).toList();
    if (added.isEmpty) return 0;
    final merged = [...current, ...added].join('\n');
    state = state.copyWith(linksText: merged, clearError: true);
    return added.length;
  }

  /// Swaps [sourceLanguage] and [targetLanguage] when [sourceLanguage] is a
  /// concrete language (not `auto`).
  bool swapLanguages() {
    final src = state.config.sourceLanguage;
    final dst = state.config.targetLanguage;
    if (src == 'auto' || src == dst) return false;
    final canSwap =
        sourceLanguageOptions.any((o) => o.value == dst) &&
        targetLanguageOptions.any((o) => o.value == src);
    if (!canSwap) return false;
    updateConfig(
      state.config.copyWith(sourceLanguage: dst, targetLanguage: src),
    );
    return true;
  }

  /// The user's own file; picking one replaces a library track.
  void setMusic(PickedMedia? music) {
    state = music == null
        ? state.copyWith(clearMusic: true)
        : state.copyWith(
            music: music,
            config: state.config.copyWith(musicTrackId: ''),
            clearError: true,
          );
  }

  /// A track of the shared library ('' = none); replaces a picked file.
  void selectMusicTrack(String trackId) {
    state = state.copyWith(
      config: state.config.copyWith(musicTrackId: trackId),
      clearMusic: trackId.isNotEmpty,
      clearError: true,
    );
  }

  /// Validation as in submitBatch() of the web app. Null when submittable.
  String? validate() {
    if (state.sourceCount == 0) {
      return state.linksText.trim().isNotEmpty
          ? 'Không tìm thấy link TikTok, Douyin, YouTube, Facebook hoặc Google Drive hợp lệ'
          : 'Hãy chọn file hoặc dán ít nhất một link video';
    }
    if (state.sourceCount > 50) return 'Mỗi batch chỉ nhận tối đa 50 video';
    if (state.config.needsMusic &&
        state.music == null &&
        state.config.musicTrackId.isEmpty) {
      return 'Hãy chọn nhạc nền trong thư viện hoặc tải file nhạc lên';
    }
    return state.config.validate();
  }

  /// Hands the batch to [submissionQueueProvider] and clears the sources, so
  /// the screen is free at once while the phone downloads and uploads in the
  /// background. Returns null when the form is invalid (reason in
  /// `state.error`), otherwise a future of the created batch (null if it
  /// failed; the pending item in the jobs list then shows why).
  Future<CreatedBatch?>? submit() {
    final invalid = validate();
    if (invalid != null) {
      state = state.copyWith(error: invalid);
      return null;
    }
    unawaited(_repo.saveConfig(state.config));
    final done = ref
        .read(submissionQueueProvider.notifier)
        .enqueue(
          SubmissionRequest(
            files: state.files,
            links: state.links,
            config: state.config,
            music: state.config.needsMusic ? state.music : null,
          ),
        );
    state = TranslateFormState(
      config: state.config.copyWith(
        batchName: const TranslationConfig().batchName,
      ),
    );
    return done;
  }
}
