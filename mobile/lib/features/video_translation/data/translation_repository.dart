import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../../core/api/api_client.dart';
import '../../../core/api/providers.dart';
import '../../../core/upload/background_uploader.dart';
import '../domain/providers.dart';
import '../domain/translation_config.dart';

final translationRepositoryProvider = Provider<TranslationRepository>(
  (ref) => TranslationRepository(
    ref.watch(apiClientProvider),
    ref.watch(backgroundUploaderProvider),
  ),
);

final translationProvidersProvider =
    FutureProvider.autoDispose<List<TranslationProviderInfo>>(
      (ref) => ref.watch(translationRepositoryProvider).translationProviders(),
    );

final ttsProvidersProvider = FutureProvider.autoDispose<List<TtsProviderInfo>>(
  (ref) => ref.watch(translationRepositoryProvider).ttsProviders(),
);

/// Background-music library (`GET /api/v1/music-tracks`), managed by admins.
final musicTracksProvider = FutureProvider.autoDispose<List<MusicTrack>>(
  (ref) => ref.watch(translationRepositoryProvider).musicTracks(),
);

/// A track of the shared background-music library.
class MusicTrack {
  const MusicTrack({
    required this.id,
    required this.title,
    required this.durationSeconds,
  });

  final String id;
  final String title;
  final double durationSeconds;

  String get durationLabel {
    final total = durationSeconds.round();
    return '${total ~/ 60}:${(total % 60).toString().padLeft(2, '0')}';
  }

  factory MusicTrack.fromJson(Map<String, dynamic> json) => MusicTrack(
    id: json['track_id'].toString(),
    title: json['title'] as String? ?? '',
    durationSeconds: (json['duration_seconds'] as num?)?.toDouble() ?? 0,
  );
}

/// A local file picked for upload, or downloaded on the phone from [sourceUrl].
class PickedMedia {
  const PickedMedia({
    required this.path,
    required this.name,
    this.size,
    this.sourceUrl,
  });

  final String path;
  final String name;
  final int? size;
  final String? sourceUrl;
}

/// `POST /api/v1/batches` response (201, or 200 with `duplicate: true` when the
/// Idempotency-Key replays an earlier request).
class CreatedBatch {
  const CreatedBatch({
    required this.batchId,
    required this.name,
    required this.jobIds,
    this.duplicate = false,
  });

  final String batchId;
  final String name;
  final List<String> jobIds;
  final bool duplicate;

  factory CreatedBatch.fromJson(Map<String, dynamic> json) => CreatedBatch(
    batchId: json['batch_id'].toString(),
    name: json['name'] as String? ?? '',
    jobIds: [
      for (final job in json['jobs'] as List? ?? const [])
        if (job is Map && job['job_id'] != null) job['job_id'].toString(),
    ],
    duplicate: json['duplicate'] == true,
  );
}

class TranslationRepository {
  TranslationRepository(this._api, this._uploader);

  static const _prefsKey = 'vidtrans.translation_config';

  final BackgroundUploader _uploader;

  final ApiClient _api;

  Future<List<TranslationProviderInfo>> translationProviders() async =>
      TranslationProviderInfo.listFromJson(
        await _api.get<Map<String, dynamic>>('/api/v1/translation-providers'),
      );

  Future<List<TtsProviderInfo>> ttsProviders() async =>
      TtsProviderInfo.listFromJson(
        await _api.get<Map<String, dynamic>>('/api/v1/tts-providers'),
      );

  Uri voicePreviewUri(String provider, String voiceKey) => _api.resolve(
    '/api/v1/tts-providers/${Uri.encodeComponent(provider)}/voices/${Uri.encodeComponent(voiceKey)}/preview',
  );

  Future<Map<String, String>> authHeaders() => _api.authHeaders();

  Future<List<MusicTrack>> musicTracks() async {
    final data = await _api.get<Map<String, dynamic>>('/api/v1/music-tracks');
    return [
      for (final item in data['items'] as List? ?? const [])
        if (item is Map) MusicTrack.fromJson(Map<String, dynamic>.from(item)),
    ];
  }

  Uri musicTrackAudioUri(String trackId) => _api.resolve(
    '/api/v1/music-tracks/${Uri.encodeComponent(trackId)}/audio',
  );

  Future<TranslationConfig> loadConfig() async {
    try {
      final raw = (await SharedPreferences.getInstance()).getString(_prefsKey);
      if (raw == null) return const TranslationConfig();
      return TranslationConfig.fromPrefs(
        Map<String, dynamic>.from(jsonDecode(raw) as Map),
      );
    } catch (_) {
      return const TranslationConfig();
    }
  }

  Future<void> saveConfig(TranslationConfig config) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsKey, jsonEncode(config.toPrefs()));
  }

  Future<CreatedBatch> createBatch({
    required List<PickedMedia> files,
    required String sourceLinks,
    required TranslationConfig config,
    PickedMedia? backgroundMusic,
    required String idempotencyKey,
    void Function(int sent, int total)? onProgress,
    CancelToken? cancelToken,
  }) async {
    final fields = {
      ...config.toFormFields(),
      if (sourceLinks.trim().isNotEmpty) 'source_links': sourceLinks.trim(),
      // One entry per `files` part, in the same order: where the phone downloaded it from.
      if (files.any((file) => file.sourceUrl != null))
        'file_source_urls': jsonEncode([
          for (final file in files) file.sourceUrl,
        ]),
    };
    final uploads = <UploadFile>[
      for (final file in files) (field: 'files', path: file.path),
      if (config.needsMusic && backgroundMusic != null)
        (field: 'background_music', path: backgroundMusic.path),
    ];
    if (uploads.isEmpty) {
      // Links only: nothing large to send, a plain request is enough.
      final form = FormData()..fields.addAll(fields.entries);
      final data = await _api.postMultipart<Map<String, dynamic>>(
        '/api/v1/batches',
        form,
        idempotencyKey: idempotencyKey,
        onProgress: onProgress,
        cancelToken: cancelToken,
      );
      return CreatedBatch.fromJson(data);
    }
    // Videos go through the OS uploader so they keep uploading in the background.
    final data = await _uploader.upload(
      '/api/v1/batches',
      fields: fields,
      files: uploads,
      idempotencyKey: idempotencyKey,
      cancelToken: cancelToken,
      onProgress: (progress) =>
          onProgress?.call((progress * 1000).round(), 1000),
    );
    return CreatedBatch.fromJson(data);
  }
}
