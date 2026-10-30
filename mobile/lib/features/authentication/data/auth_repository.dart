import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_client.dart';
import '../../../core/api/providers.dart';
import '../../../core/auth/account.dart';
import '../../../core/storage/token_storage.dart';

final authRepositoryProvider = Provider<AuthRepository>(
  (ref) => AuthRepository(
    ref.watch(apiClientProvider),
    ref.watch(tokenStorageProvider),
  ),
);

class ServerAuthStatus {
  const ServerAuthStatus({required this.enabled, required this.configured});

  final bool enabled;
  final bool configured;

  factory ServerAuthStatus.fromJson(Map<String, dynamic> json) =>
      ServerAuthStatus(
        enabled: json['enabled'] == true,
        configured: json['configured'] == true,
      );
}

class AuthRepository {
  AuthRepository(this._api, this._tokens);

  final ApiClient _api;
  final TokenStorage _tokens;

  Future<ServerAuthStatus> status() async => ServerAuthStatus.fromJson(
    await _api.get<Map<String, dynamic>>('/api/v1/auth/status'),
  );

  /// `client=native` makes the server return the refresh token in JSON
  /// instead of setting cookies.
  Future<Account> login({
    required String username,
    required String password,
  }) async {
    final data = await _api.post<Map<String, dynamic>>(
      '/api/v1/auth/token',
      data: {
        'username': username,
        'password': password,
        'client': 'native',
        'remember': 'true',
      },
      options: ApiClient.anonymous.copyWith(
        contentType: Headers.formUrlEncodedContentType,
      ),
    );
    await _tokens.save(
      AuthTokens(
        accessToken: data['access_token'] as String,
        refreshToken: data['refresh_token'] as String,
      ),
    );
    return me();
  }

  Future<Account> me() async =>
      Account.fromJson(await _api.get<Map<String, dynamic>>('/api/v1/me'));

  Future<bool> hasSession() async => await _tokens.read() != null;

  /// Native logout only needs to forget the tokens (docs/mobile-api.md).
  Future<void> logout() => _tokens.clear();
}
