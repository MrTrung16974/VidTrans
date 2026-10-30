import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../features/authentication/data/auth_repository.dart';
import '../errors/api_exception.dart';
import '../push/push_service.dart';
import 'account.dart';

/// Signed-in account, or null when signed out. Loading while the stored
/// session is being checked at startup.
final sessionProvider = AsyncNotifierProvider<SessionController, Account?>(
  SessionController.new,
);

/// Whether the server requires OAuth2/JWT. Null while startup discovery is in progress.
final authEnabledProvider = StateProvider<bool?>((ref) => null);

class SessionController extends AsyncNotifier<Account?> {
  AuthRepository get _auth => ref.read(authRepositoryProvider);

  @override
  Future<Account?> build() async {
    final authStatus = await _auth.status();
    ref.read(authEnabledProvider.notifier).state = authStatus.enabled;
    if (!authStatus.enabled) {
      // A server can be switched from authenticated production mode back to
      // local development. Do not keep sending a stale Bearer token there.
      await _auth.logout();
      return _auth.me();
    }
    if (!await _auth.hasSession()) return null;
    try {
      return await _auth.me();
    } on ApiException catch (error) {
      if (error.statusCode == 401 || error.statusCode == 403) {
        await _auth.logout();
        return null;
      }
      rethrow;
    }
  }

  Future<void> login(String username, String password) async {
    final account = await _auth.login(username: username, password: password);
    state = AsyncData(account);
  }

  Future<void> reload() async {
    state = AsyncData(await _auth.me());
  }

  Future<void> logout() async {
    if (ref.read(authEnabledProvider) == false) {
      state = AsyncData(await _auth.me());
      return;
    }
    // Unregister push while the request can still be authorised.
    await ref.read(pushServiceProvider).onSigningOut();
    await _auth.logout();
    state = const AsyncData(null);
  }

  /// The refresh token was rejected; tokens are already cleared.
  void expire() => state = const AsyncData(null);
}
