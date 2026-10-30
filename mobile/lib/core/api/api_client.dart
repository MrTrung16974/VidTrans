import 'dart:async';

import 'package:dio/dio.dart';

import '../errors/api_exception.dart';
import '../storage/token_storage.dart';

/// Dio wrapper that sends `Authorization: Bearer`, refreshes the access token
/// once on 401 (shared by all concurrent requests) and replays the request.
class ApiClient {
  ApiClient({
    required String baseUrl,
    required this.tokens,
    this.onSessionExpired,
    HttpClientAdapter? adapter,
  })  : dio = Dio(_options(baseUrl)),
        _refreshDio = Dio(_options(baseUrl)) {
    if (adapter != null) {
      dio.httpClientAdapter = adapter;
      _refreshDio.httpClientAdapter = adapter;
    }
    dio.interceptors.add(InterceptorsWrapper(onRequest: _onRequest, onError: _onError));
  }

  static const _retriedKey = 'vidtrans.retried';
  static const _skipAuthKey = 'vidtrans.skipAuth';

  final Dio dio;
  final Dio _refreshDio;
  final TokenStorage tokens;

  /// Called when the refresh token is rejected; the app returns to login.
  final void Function()? onSessionExpired;

  Future<String?>? _refreshing;

  static BaseOptions _options(String baseUrl) => BaseOptions(
        baseUrl: baseUrl,
        connectTimeout: const Duration(seconds: 15),
        receiveTimeout: const Duration(seconds: 60),
        headers: {'Accept': 'application/json'},
      );

  /// Options for requests that must not carry a token (login, register...).
  static Options get anonymous => Options(extra: {_skipAuthKey: true});

  Future<void> _onRequest(RequestOptions options, RequestInterceptorHandler handler) async {
    if (options.extra[_skipAuthKey] != true) {
      final current = await tokens.read();
      if (current != null) options.headers['Authorization'] = 'Bearer ${current.accessToken}';
    }
    handler.next(options);
  }

  Future<void> _onError(DioException error, ErrorInterceptorHandler handler) async {
    final options = error.requestOptions;
    final canRetry = error.response?.statusCode == 401 &&
        options.extra[_skipAuthKey] != true &&
        options.extra[_retriedKey] != true;
    if (!canRetry) return handler.next(error);

    try {
      final accessToken = await refreshAccessToken();
      if (accessToken == null) return handler.next(error);
      options.extra[_retriedKey] = true;
      options.headers['Authorization'] = 'Bearer $accessToken';
      // A sent FormData is finalized; replaying needs a fresh copy.
      if (options.data is FormData) options.data = (options.data as FormData).clone();
      handler.resolve(await dio.fetch<dynamic>(options));
    } on DioException catch (retryError) {
      handler.next(retryError);
    }
  }

  /// Returns a new access token, or null when the session is over.
  /// Concurrent callers share a single in-flight refresh request.
  Future<String?> refreshAccessToken() {
    return _refreshing ??= _refresh().whenComplete(() => _refreshing = null);
  }

  Future<String?> _refresh() async {
    final current = await tokens.read();
    if (current == null) return null;
    try {
      final response = await _refreshDio.post<Map<String, dynamic>>(
        '/api/v1/auth/refresh',
        data: {'refresh_token': current.refreshToken},
      );
      final accessToken = response.data?['access_token'] as String?;
      if (accessToken == null) throw const ApiException('Phản hồi làm mới phiên không hợp lệ');
      await tokens.updateAccessToken(accessToken);
      return accessToken;
    } on DioException catch (error) {
      final status = error.response?.statusCode;
      if (status == 401 || status == 403) {
        // Expired, password changed or account locked: the session is over.
        await tokens.clear();
        onSessionExpired?.call();
        return null;
      }
      // Network trouble: keep the tokens, the next request will try again.
      rethrow;
    }
  }

  Future<T> get<T>(String path, {Map<String, dynamic>? query}) =>
      _send(() => dio.get<T>(path, queryParameters: query));

  Future<T> post<T>(String path, {Object? data, Options? options}) =>
      _send(() => dio.post<T>(path, data: data, options: options));

  Future<T> put<T>(String path, {Object? data}) => _send(() => dio.put<T>(path, data: data));

  Future<T> patch<T>(String path, {Object? data}) => _send(() => dio.patch<T>(path, data: data));

  /// Multipart upload with progress. [idempotencyKey] lets the server return
  /// the original result when a flaky network makes the app send twice.
  Future<T> postMultipart<T>(
    String path,
    FormData data, {
    String? idempotencyKey,
    void Function(int sent, int total)? onProgress,
    CancelToken? cancelToken,
  }) =>
      _send(() => dio.post<T>(
            path,
            data: data,
            options: Options(
              headers: {'Idempotency-Key': ?idempotencyKey},
              sendTimeout: const Duration(hours: 1),
            ),
            onSendProgress: onProgress,
            cancelToken: cancelToken,
          ));

  /// Absolute URL for a server path such as `/api/v1/jobs/{id}/artifacts/video`.
  Uri resolve(String path) => Uri.parse(dio.options.baseUrl).resolve(path);

  /// Headers for clients outside Dio (video_player, Image.network, downloads).
  Future<Map<String, String>> authHeaders() async {
    final current = await tokens.read();
    return {if (current != null) 'Authorization': 'Bearer ${current.accessToken}'};
  }

  Future<T> delete<T>(String path) => _send(() => dio.delete<T>(path));

  Future<T> _send<T>(Future<Response<T>> Function() request) async {
    try {
      return (await request()).data as T;
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }
}
