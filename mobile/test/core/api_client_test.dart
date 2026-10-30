import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/core/api/api_client.dart';
import 'package:vidtrans_mobile/core/errors/api_exception.dart';
import 'package:vidtrans_mobile/core/storage/token_storage.dart';

/// Accepts only "Bearer fresh"; counts refresh calls.
class _FakeServer implements HttpClientAdapter {
  _FakeServer({this.refreshStatus = 200});

  final int refreshStatus;
  int refreshCalls = 0;

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    if (options.path == '/api/v1/auth/refresh') {
      refreshCalls++;
      await Future<void>.delayed(const Duration(milliseconds: 20));
      return refreshStatus == 200
          ? _json(200, {'access_token': 'fresh', 'token_type': 'bearer'})
          : _json(refreshStatus, {'detail': 'Phiên đã hết hạn'});
    }
    if (options.headers['Authorization'] != 'Bearer fresh') return _json(401, {'detail': 'Unauthorized'});
    return _json(200, {'path': options.path});
  }

  ResponseBody _json(int status, Object body) => ResponseBody.fromString(
        jsonEncode(body),
        status,
        headers: {Headers.contentTypeHeader: [Headers.jsonContentType]},
      );

  @override
  void close({bool force = false}) {}
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() => FlutterSecureStorage.setMockInitialValues({
        'vidtrans.access_token': 'stale',
        'vidtrans.refresh_token': 'refresh',
      }));

  test('concurrent 401s share one refresh and are replayed', () async {
    final server = _FakeServer();
    final tokens = TokenStorage();
    final api = ApiClient(baseUrl: 'http://test', tokens: tokens, adapter: server);

    final results = await Future.wait([
      api.get<Map<String, dynamic>>('/api/v1/me'),
      api.get<Map<String, dynamic>>('/api/v1/jobs'),
      api.get<Map<String, dynamic>>('/api/v1/batches'),
    ]);

    expect(server.refreshCalls, 1);
    expect(results.map((r) => r['path']), ['/api/v1/me', '/api/v1/jobs', '/api/v1/batches']);
    expect((await tokens.read())!.accessToken, 'fresh');
  });

  test('rejected refresh clears tokens and ends the session', () async {
    final server = _FakeServer(refreshStatus: 401);
    final tokens = TokenStorage();
    var expired = 0;
    final api = ApiClient(baseUrl: 'http://test', tokens: tokens, adapter: server, onSessionExpired: () => expired++);

    await expectLater(api.get<dynamic>('/api/v1/me'), throwsA(isA<ApiException>()));
    expect(expired, 1);
    expect(await tokens.read(), isNull);
  });
}
