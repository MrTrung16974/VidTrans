import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_client.dart';
import '../../../core/api/providers.dart';
import '../../../core/auth/session_controller.dart';
import '../domain/app_menu.dart';

final menuRepositoryProvider = Provider<MenuRepository>(
  (ref) => MenuRepository(ref.watch(apiClientProvider)),
);

/// The dock menus for the signed-in account; refetched when the session changes.
/// While it loads or if it fails, HomeShell falls back to its built-in dock.
final mobileMenuProvider = FutureProvider<AppMenuConfig?>((ref) async {
  final account = ref.watch(sessionProvider).valueOrNull;
  if (account == null) return null;
  return ref.read(menuRepositoryProvider).mobileMenus();
});

class MenuRepository {
  MenuRepository(this._api);

  final ApiClient _api;

  Future<AppMenuConfig> mobileMenus() async => AppMenuConfig.fromJson(
    await _api.get<Map<String, dynamic>>(
      '/api/v1/me/menus',
      query: {'platform': 'mobile'},
    ),
  );
}
