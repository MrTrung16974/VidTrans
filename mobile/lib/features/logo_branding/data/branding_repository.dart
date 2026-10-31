import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_client.dart';
import '../../../core/api/providers.dart';
import '../domain/branding_models.dart';

final brandingRepositoryProvider = Provider<BrandingRepository>(
  (ref) => BrandingRepository(ref.watch(apiClientProvider)),
);

class BrandingRepository {
  BrandingRepository(this._api);

  final ApiClient _api;

  Future<BrandingSettings> settings() async =>
      BrandingSettings.fromJson(await _api.get<Map<String, dynamic>>('/api/v1/me/branding'));

  Future<BrandingSettings> saveConfig(BrandingConfig config) async =>
      BrandingSettings.fromJson(await _api.put<Map<String, dynamic>>('/api/v1/me/branding', data: config.toJson()));

  /// [kind] is `logo` or `template`; both accept a PNG/JPEG/WebP up to 5 MB.
  Future<BrandingSettings> uploadImage(String kind, String filePath) async {
    final form = FormData.fromMap({'file': await MultipartFile.fromFile(filePath)});
    return BrandingSettings.fromJson(await _api.postMultipart<Map<String, dynamic>>('/api/v1/me/branding/$kind', form));
  }

  Future<BrandingSettings> deleteImage(String kind) async =>
      BrandingSettings.fromJson(await _api.delete<Map<String, dynamic>>('/api/v1/me/branding/$kind'));
}
