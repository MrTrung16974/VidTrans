import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import '../api/api_client.dart';
import '../api/providers.dart';
import '../errors/api_exception.dart';

final artifactDownloaderProvider =
    Provider<ArtifactDownloader>((ref) => ArtifactDownloader(ref.watch(apiClientProvider)));

/// Downloads authenticated results (MP4, SRT...) into the app's documents
/// folder, then hands them to the system share sheet ("Save video", Files...).
class ArtifactDownloader {
  ArtifactDownloader(this._api);

  final ApiClient _api;

  Future<File> download(
    String path, {
    required String fileName,
    void Function(int received, int total)? onProgress,
    CancelToken? cancelToken,
  }) async {
    final dir = Directory('${(await getApplicationDocumentsDirectory()).path}/downloads');
    await dir.create(recursive: true);
    final file = File('${dir.path}/${_safeName(fileName)}');
    try {
      // Goes through the Dio interceptors, so an expired token is refreshed.
      await _api.dio.download(path, file.path, onReceiveProgress: onProgress, cancelToken: cancelToken);
    } on DioException catch (error) {
      if (await file.exists()) await file.delete();
      throw ApiException.fromDio(error);
    }
    return file;
  }

  Future<void> share(File file, {String? text}) =>
      SharePlus.instance.share(ShareParams(files: [XFile(file.path)], text: text));

  static String _safeName(String name) => name.replaceAll(RegExp(r'[\\/:*?"<>|]'), '_');
}
