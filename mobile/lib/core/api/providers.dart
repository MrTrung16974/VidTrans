import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/session_controller.dart';
import '../config/env.dart';
import '../storage/token_storage.dart';
import 'api_client.dart';

final tokenStorageProvider = Provider<TokenStorage>((ref) => TokenStorage());

final apiClientProvider = Provider<ApiClient>((ref) {
  return ApiClient(
    baseUrl: Env.apiBase,
    tokens: ref.watch(tokenStorageProvider),
    onSessionExpired: () => ref.read(sessionProvider.notifier).expire(),
  );
});
