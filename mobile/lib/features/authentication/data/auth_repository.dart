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

/// A random trial account; the password is only ever returned once.
class GuestCredentials {
  const GuestCredentials({
    required this.email,
    required this.password,
    required this.quotaMinutes,
  });

  final String email;
  final String password;
  final int? quotaMinutes;
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
    bool remember = true,
  }) async {
    final data = await _api.post<Map<String, dynamic>>(
      '/api/v1/auth/token',
      data: {
        'username': username,
        'password': password,
        'client': 'native',
        'remember': remember ? 'true' : 'false',
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

  /// Creates a new Free account and signs in immediately with native tokens.
  Future<Account> register({
    required String displayName,
    required String email,
    required String password,
  }) async {
    await _api.post<Map<String, dynamic>>(
      '/api/v1/auth/register',
      data: {
        'display_name': displayName,
        'email': email,
        'password': password,
      },
      options: ApiClient.anonymous.copyWith(
        contentType: Headers.formUrlEncodedContentType,
      ),
    );
    return login(username: email, password: password);
  }

  /// Creates a random trial account. The caller signs in with the returned
  /// credentials after showing them, so the guest can save them first.
  Future<GuestCredentials> createGuest() async {
    final data = await _api.post<Map<String, dynamic>>(
      '/api/v1/auth/guest',
      options: ApiClient.anonymous,
    );
    final credentials = data['credentials'] as Map<String, dynamic>;
    return GuestCredentials(
      email: credentials['email'] as String,
      password: credentials['password'] as String,
      quotaMinutes: (data['quota_minutes'] as num?)?.toInt(),
    );
  }

  /// Sends a 6-digit password reset code to [email].
  Future<String> requestPasswordReset(String email) async {
    final data = await _api.post<Map<String, dynamic>>(
      '/api/v1/auth/password-reset/request',
      data: {'email': email},
      options: ApiClient.anonymous.copyWith(
        contentType: Headers.formUrlEncodedContentType,
      ),
    );
    return (data['message'] as String?) ??
        'Mã xác nhận 6 chữ số đã được gửi tới email của bạn.';
  }

  /// Confirms password reset with the 6-digit [code] and sets [newPassword].
  Future<String> confirmPasswordReset({
    required String email,
    required String code,
    required String newPassword,
  }) async {
    final data = await _api.post<Map<String, dynamic>>(
      '/api/v1/auth/password-reset/confirm',
      data: {
        'email': email,
        'code': code,
        'new_password': newPassword,
      },
      options: ApiClient.anonymous.copyWith(
        contentType: Headers.formUrlEncodedContentType,
      ),
    );
    return (data['message'] as String?) ??
        'Đặt lại mật khẩu thành công. Vui lòng đăng nhập lại.';
  }

  Future<Account> me() async =>
      Account.fromJson(await _api.get<Map<String, dynamic>>('/api/v1/me'));

  Future<bool> hasSession() async => await _tokens.read() != null;

  /// Native logout only needs to forget the tokens (docs/mobile-api.md).
  Future<void> logout() => _tokens.clear();
}
