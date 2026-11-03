import 'dart:async';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../jobs/presentation/providers/jobs_providers.dart';
import '../../../logo_branding/presentation/providers/branding_providers.dart';
import '../../../source_download/data/phone_video_downloader.dart';
import '../../../source_download/domain/download_policy.dart';
import '../../data/translation_repository.dart';
import '../../domain/translation_config.dart';

enum SubmissionPhase { waiting, downloading, uploading, creating, failed }

/// A batch the user started that is still being downloaded on the phone or
/// uploaded. It is shown in the jobs list until the server creates its jobs.
class PendingSubmission {
  const PendingSubmission({
    required this.id,
    required this.name,
    required this.sourceCount,
    this.phase = SubmissionPhase.waiting,
    this.progress = 0,
    this.note,
    this.error,
  });

  final String id;
  final String name;
  final int sourceCount;
  final SubmissionPhase phase;

  /// 0..1 of the current phone download or of the upload.
  final double progress;

  /// "TikTok 1/3" while links are downloaded on the phone.
  final String? note;
  final String? error;

  bool get failed => phase == SubmissionPhase.failed;

  PendingSubmission copyWith({
    SubmissionPhase? phase,
    double? progress,
    String? note,
    bool clearNote = false,
    String? error,
  }) => PendingSubmission(
    id: id,
    name: name,
    sourceCount: sourceCount,
    phase: phase ?? this.phase,
    progress: progress ?? this.progress,
    note: clearNote ? null : (note ?? this.note),
    error: error,
  );
}

/// Everything a batch needs, copied from the form when the user pressed start.
class SubmissionRequest {
  const SubmissionRequest({
    required this.files,
    required this.links,
    required this.config,
    this.music,
  });

  final List<PickedMedia> files;
  final List<String> links;
  final TranslationConfig config;
  final PickedMedia? music;
}

class _Task {
  _Task(this.request) : idempotencyKey = const Uuid().v4();

  final SubmissionRequest request;

  /// Kept when a retry may hit a batch the server already created.
  String? idempotencyKey;

  /// Links already downloaded on the phone, reused by a retry.
  final Map<String, PickedMedia> phoneDownloads = {};
  CancelToken? cancelToken;
  bool cancelled = false;
  Completer<CreatedBatch?> done = Completer();
}

final submissionQueueProvider =
    NotifierProvider<SubmissionQueue, List<PendingSubmission>>(
      SubmissionQueue.new,
    );

/// Runs started batches one at a time in the background (download on the
/// phone, upload, create), so the translate screen is free right away.
class SubmissionQueue extends Notifier<List<PendingSubmission>> {
  final Map<String, _Task> _tasks = {};
  bool _running = false;

  TranslationRepository get _repo => ref.read(translationRepositoryProvider);

  @override
  List<PendingSubmission> build() => const [];

  /// Queues the batch and returns a future of its result (null when it failed
  /// or was cancelled; the reason stays on the pending item).
  Future<CreatedBatch?> enqueue(SubmissionRequest request) {
    final id = const Uuid().v4();
    final task = _Task(request);
    _tasks[id] = task;
    final name = request.config.batchName.trim();
    state = [
      ...state,
      PendingSubmission(
        id: id,
        name: name.isEmpty ? 'Batch mới' : name,
        sourceCount: request.files.length + request.links.length,
      ),
    ];
    unawaited(_pump());
    return task.done.future;
  }

  Future<CreatedBatch?> retry(String id) {
    final task = _tasks[id];
    if (task == null) return Future.value();
    task
      ..cancelled = false
      ..done = Completer();
    _update(
      id,
      (s) => s.copyWith(
        phase: SubmissionPhase.waiting,
        progress: 0,
        clearNote: true,
      ),
    );
    unawaited(_pump());
    return task.done.future;
  }

  void cancel(String id) {
    final task = _tasks[id];
    if (task == null) return;
    task.cancelled = true;
    task.cancelToken?.cancel();
    // Not started yet: nothing to undo.
    if (_find(id)?.phase == SubmissionPhase.waiting) {
      _fail(id, task, 'Đã hủy tải lên', resetKey: true);
    }
  }

  /// Removes a failed or cancelled item and the videos downloaded for it.
  void dismiss(String id) {
    final task = _tasks.remove(id);
    if (task == null) return;
    task.cancelled = true;
    task.cancelToken?.cancel();
    for (final file in task.phoneDownloads.values) {
      _deleteQuietly(file);
    }
    state = [
      for (final s in state)
        if (s.id != id) s,
    ];
  }

  Future<void> _pump() async {
    if (_running) return;
    _running = true;
    try {
      while (true) {
        final next = state
            .where((s) => s.phase == SubmissionPhase.waiting)
            .firstOrNull;
        if (next == null) break;
        final task = _tasks[next.id];
        if (task == null) break;
        await _run(next.id, task);
      }
    } finally {
      _running = false;
    }
  }

  Future<void> _run(String id, _Task task) async {
    task.idempotencyKey ??= const Uuid().v4();
    task.cancelToken = CancelToken();
    var phase = SubmissionPhase.downloading;
    _update(id, (s) => s.copyWith(phase: phase, progress: 0, clearNote: true));
    try {
      final serverLinks = await _downloadOnPhone(id, task);
      if (task.cancelled) throw const ApiException('Đã hủy tải lên');
      phase = SubmissionPhase.uploading;
      _update(
        id,
        (s) => s.copyWith(phase: phase, progress: 0, clearNote: true),
      );
      final request = task.request;
      final phoneFiles = [
        for (final link in request.links) ?task.phoneDownloads[link],
      ];
      // The choice is remembered, but only honoured while the account has a logo.
      final applyLogo = request.config.applyLogo && await _accountHasLogo();
      final batch = await _repo.createBatch(
        files: [...request.files, ...phoneFiles],
        sourceLinks: serverLinks.join('\n'),
        config: request.config.copyWith(applyLogo: applyLogo),
        backgroundMusic: request.music,
        idempotencyKey: task.idempotencyKey!,
        cancelToken: task.cancelToken,
        onProgress: (sent, total) {
          if (total <= 0) return;
          final progress = sent / total;
          phase = progress >= 1
              ? SubmissionPhase.creating
              : SubmissionPhase.uploading;
          _update(id, (s) => s.copyWith(phase: phase, progress: progress));
        },
      );
      for (final file in task.phoneDownloads.values) {
        _deleteQuietly(file);
      }
      _tasks.remove(id);
      state = [
        for (final s in state)
          if (s.id != id) s,
      ];
      // Refresh in place when the list is open; it loads fresh otherwise.
      if (ref.exists(jobListProvider))
        unawaited(ref.read(jobListProvider.notifier).refresh());
      task.done.complete(batch);
    } catch (error) {
      final uploadFinished = phase == SubmissionPhase.creating;
      final uploadStarted = phase != SubmissionPhase.downloading;
      // Keep the key whenever the server may have created the batch (network
      // error, 5xx, 409 "still processing", cancel after the upload finished).
      // A rejected request (4xx) or a cancel mid-upload makes the next try new.
      final status = error is ApiException ? error.statusCode : null;
      final rejected = status != null && status < 500 && status != 409;
      _fail(
        id,
        task,
        task.cancelled ? 'Đã hủy tải lên' : errorMessage(error),
        resetKey:
            rejected || !uploadStarted || (task.cancelled && !uploadFinished),
      );
    } finally {
      task.cancelToken = null;
    }
  }

  void _fail(String id, _Task task, String message, {required bool resetKey}) {
    if (resetKey) task.idempotencyKey = null;
    _update(
      id,
      (s) => s.copyWith(
        phase: SubmissionPhase.failed,
        clearNote: true,
        error: message,
      ),
    );
    if (!task.done.isCompleted) task.done.complete(null);
  }

  /// Downloads the links the server's policy assigns to the phone, one at a
  /// time with a short pause, like a person would. Returns the links left for
  /// the server: those it downloads itself, and those the phone could not get.
  Future<List<String>> _downloadOnPhone(String id, _Task task) async {
    final links = task.request.links;
    if (links.isEmpty) return const [];
    final policy = await ref.read(downloadPolicyRepositoryProvider).load();
    final downloader = ref.read(phoneVideoDownloaderProvider);
    final pending = [
      for (final link in links)
        if (policy.onPhone(link) && !task.phoneDownloads.containsKey(link))
          link,
    ];
    for (final (index, link) in pending.indexed) {
      if (task.cancelled) break;
      if (index > 0)
        await Future<void>.delayed(const Duration(milliseconds: 1500));
      _update(
        id,
        (s) => s.copyWith(
          progress: 0,
          note:
              '${socialPlatformOf(link) ?? 'Video'} ${index + 1}/${pending.length}',
        ),
      );
      try {
        final video = await downloader.download(
          link,
          cancelToken: task.cancelToken,
          onProgress: (progress) =>
              _update(id, (s) => s.copyWith(progress: progress)),
        );
        task.phoneDownloads[link] = PickedMedia(
          path: video.path,
          name: video.name,
          size: video.size,
          sourceUrl: video.sourceUrl,
        );
      } on PhoneDownloadException {
        // Left for the server, which tries it with its own downloader.
      }
    }
    return [
      for (final link in links)
        if (!task.phoneDownloads.containsKey(link)) link,
    ];
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

  PendingSubmission? _find(String id) =>
      state.where((s) => s.id == id).firstOrNull;

  void _update(
    String id,
    PendingSubmission Function(PendingSubmission) change,
  ) {
    state = [for (final s in state) s.id == id ? change(s) : s];
  }

  static void _deleteQuietly(PickedMedia file) {
    unawaited(File(file.path).delete().then((_) {}, onError: (_) {}));
  }
}
