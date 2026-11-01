import 'dart:async';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../logo_branding/presentation/providers/branding_providers.dart';
import '../../../source_download/data/phone_video_downloader.dart';
import '../../../source_download/domain/download_policy.dart';
import '../../data/translation_repository.dart';
import '../../domain/options.dart';
import '../../domain/translation_config.dart';

enum UploadPhase { idle, downloading, uploading, creating }

class TranslateFormState {
  const TranslateFormState({
    this.config = const TranslationConfig(),
    this.files = const [],
    this.linksText = '',
    this.music,
    this.phase = UploadPhase.idle,
    this.progress = 0,
    this.downloadNote,
    this.error,
  });

  final TranslationConfig config;
  final List<PickedMedia> files;
  final String linksText;
  final PickedMedia? music;
  final UploadPhase phase;

  /// Upload progress 0..1, or the current phone download's while [phase] is downloading.
  final double progress;

  /// "TikTok 1/3" while links are downloaded on the phone.
  final String? downloadNote;
  final String? error;

  List<String> get links => extractLinks(linksText);
  int get sourceCount => files.length + links.length;
  bool get busy => phase != UploadPhase.idle;

  TranslateFormState copyWith({
    TranslationConfig? config,
    List<PickedMedia>? files,
    String? linksText,
    PickedMedia? music,
    bool clearMusic = false,
    UploadPhase? phase,
    double? progress,
    String? downloadNote,
    String? error,
    bool clearError = false,
  }) =>
      TranslateFormState(
        config: config ?? this.config,
        files: files ?? this.files,
        linksText: linksText ?? this.linksText,
        music: clearMusic ? null : (music ?? this.music),
        phase: phase ?? this.phase,
        progress: progress ?? this.progress,
        downloadNote: downloadNote ?? this.downloadNote,
        error: clearError ? null : (error ?? this.error),
      );
}

final translateFormProvider =
    NotifierProvider<TranslateFormController, TranslateFormState>(TranslateFormController.new);

/// Form state survives tab switches (not auto-disposed) so an upload keeps
/// running while the user looks at other tabs.
class TranslateFormController extends Notifier<TranslateFormState> {
  TranslationRepository get _repo => ref.read(translationRepositoryProvider);

  CancelToken? _cancelToken;
  bool _cancelled = false;

  /// One key per submission: kept when the user retries after a network error
  /// so the server returns the batch it may already have created, and reset
  /// whenever the sources or settings change.
  String? _idempotencyKey;

  /// Links already downloaded on the phone, kept so a retry after an upload
  /// error does not download them again. Files are deleted once uploaded.
  final Map<String, PickedMedia> _phoneDownloads = {};

  @override
  TranslateFormState build() {
    unawaited(_restore());
    return const TranslateFormState();
  }

  Future<void> _restore() async {
    final config = await _repo.loadConfig();
    state = state.copyWith(config: config.copyWith(batchName: state.config.batchName));
  }

  void updateConfig(TranslationConfig config) {
    _idempotencyKey = null;
    state = state.copyWith(config: config, clearError: true);
  }

  void addFiles(List<PickedMedia> files) {
    final known = state.files.map((f) => f.path).toSet();
    _idempotencyKey = null;
    state = state.copyWith(files: [...state.files, ...files.where((f) => known.add(f.path))], clearError: true);
  }

  void removeFile(PickedMedia file) {
    _idempotencyKey = null;
    state = state.copyWith(files: state.files.where((f) => f.path != file.path).toList());
  }

  void setLinks(String text) {
    _idempotencyKey = null;
    state = state.copyWith(linksText: text, clearError: true);
    final links = state.links.toSet();
    for (final link in _phoneDownloads.keys.where((link) => !links.contains(link)).toList()) {
      _deleteQuietly(_phoneDownloads.remove(link)!);
    }
  }

  void setMusic(PickedMedia? music) {
    _idempotencyKey = null;
    state = music == null ? state.copyWith(clearMusic: true) : state.copyWith(music: music, clearError: true);
  }

  /// Validation as in submitBatch() of the web app. Null when submittable.
  String? validate() {
    if (state.sourceCount == 0) {
      return state.linksText.trim().isNotEmpty
          ? 'Không tìm thấy link TikTok, Douyin, YouTube hoặc Facebook hợp lệ'
          : 'Hãy chọn file hoặc dán ít nhất một link video';
    }
    if (state.sourceCount > 50) return 'Mỗi batch chỉ nhận tối đa 50 video';
    if (state.config.needsMusic && state.music == null) return 'Mode 3 cần một file nhạc nền';
    return state.config.validate();
  }

  /// Uploads and creates the batch. Returns the created batch, or null when
  /// it failed or was cancelled (the reason is in `state.error`).
  Future<CreatedBatch?> submit() async {
    if (state.busy) return null;
    final invalid = validate();
    if (invalid != null) {
      state = state.copyWith(error: invalid);
      return null;
    }
    _idempotencyKey ??= const Uuid().v4();
    _cancelled = false;
    _cancelToken = CancelToken();
    state = state.copyWith(phase: UploadPhase.downloading, progress: 0, clearError: true);
    unawaited(_repo.saveConfig(state.config));
    try {
      final serverLinks = await _downloadOnPhone();
      if (_cancelled) throw const ApiException('Đã hủy tải lên');
      state = state.copyWith(phase: UploadPhase.uploading, progress: 0);
      final phoneFiles = [for (final link in state.links) ?_phoneDownloads[link]];
      // The choice is remembered, but only honoured while the account has a logo.
      final applyLogo = state.config.applyLogo && await _accountHasLogo();
      final batch = await _repo.createBatch(
        files: [...state.files, ...phoneFiles],
        sourceLinks: serverLinks.join('\n'),
        config: state.config.copyWith(applyLogo: applyLogo),
        backgroundMusic: state.music,
        idempotencyKey: _idempotencyKey!,
        cancelToken: _cancelToken,
        onProgress: (sent, total) {
          if (total <= 0) return;
          final progress = sent / total;
          state = state.copyWith(
            progress: progress,
            phase: progress >= 1 ? UploadPhase.creating : UploadPhase.uploading,
          );
        },
      );
      _idempotencyKey = null;
      for (final file in _phoneDownloads.values) {
        _deleteQuietly(file);
      }
      _phoneDownloads.clear();
      state = TranslateFormState(config: state.config.copyWith(batchName: const TranslationConfig().batchName));
      return batch;
    } catch (error) {
      final uploadFinished = state.phase == UploadPhase.creating;
      final uploadStarted = state.phase != UploadPhase.downloading;
      state = state.copyWith(
        phase: UploadPhase.idle,
        error: _cancelled ? 'Đã hủy tải lên' : errorMessage(error),
      );
      // Keep the key whenever the server may have created the batch (network
      // error, 5xx, 409 "still processing", cancel after the upload finished).
      // A rejected request (4xx) or a cancel mid-upload makes the next try new.
      final status = error is ApiException ? error.statusCode : null;
      final rejected = status != null && status < 500 && status != 409;
      if (rejected || !uploadStarted || (_cancelled && !uploadFinished)) _idempotencyKey = null;
      return null;
    } finally {
      _cancelToken = null;
    }
  }

  /// Downloads the links the server's policy assigns to the phone, one at a
  /// time with a short pause, like a person would. Returns the links left for
  /// the server: those it downloads itself, and those the phone could not get.
  Future<List<String>> _downloadOnPhone() async {
    final policy = await ref.read(downloadPolicyRepositoryProvider).load();
    final downloader = ref.read(phoneVideoDownloaderProvider);
    final links = state.links;
    final pending = [
      for (final link in links)
        if (policy.onPhone(link) && !_phoneDownloads.containsKey(link)) link,
    ];
    for (final (index, link) in pending.indexed) {
      if (_cancelled) break;
      if (index > 0) await Future<void>.delayed(const Duration(milliseconds: 1500));
      state = state.copyWith(
        progress: 0,
        downloadNote: '${socialPlatformOf(link) ?? 'Video'} ${index + 1}/${pending.length}',
      );
      try {
        final video = await downloader.download(
          link,
          cancelToken: _cancelToken,
          onProgress: (progress) => state = state.copyWith(progress: progress),
        );
        _phoneDownloads[link] =
            PickedMedia(path: video.path, name: video.name, size: video.size, sourceUrl: video.sourceUrl);
      } on PhoneDownloadException {
        // Left for the server, which tries it with its own downloader.
      }
    }
    return [for (final link in links) if (!_phoneDownloads.containsKey(link)) link];
  }

  /// Waits for the branding settings instead of reading a value that may not
  /// have loaded yet, which would silently send `apply_logo=false`.
  Future<bool> _accountHasLogo() async {
    try {
      return (await ref.read(brandingSettingsProvider.future)).logo != null;
    } catch (_) {
      return false;
    }
  }

  static void _deleteQuietly(PickedMedia file) {
    unawaited(File(file.path).delete().then((_) {}, onError: (_) {}));
  }

  void cancel() {
    _cancelled = true;
    _cancelToken?.cancel();
  }
}
