import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/branding_repository.dart';
import '../../domain/branding_models.dart';

final brandingSettingsProvider =
    AsyncNotifierProvider<BrandingSettingsController, BrandingSettings>(BrandingSettingsController.new);

class BrandingSettingsController extends AsyncNotifier<BrandingSettings> {
  BrandingRepository get _repo => ref.read(brandingRepositoryProvider);

  @override
  Future<BrandingSettings> build() => _repo.settings();

  Future<void> saveConfig(BrandingConfig config) async => state = AsyncData(await _repo.saveConfig(config));

  Future<void> uploadImage(String kind, String path) async => state = AsyncData(await _repo.uploadImage(kind, path));

  Future<void> deleteImage(String kind) async => state = AsyncData(await _repo.deleteImage(kind));
}
