import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/core/api/api_client.dart';
import 'package:vidtrans_mobile/core/auth/account.dart';
import 'package:vidtrans_mobile/core/auth/session_controller.dart';
import 'package:vidtrans_mobile/core/storage/token_storage.dart';
import 'package:vidtrans_mobile/features/authentication/data/auth_repository.dart';

class _FakeAuthRepository extends AuthRepository {
  _FakeAuthRepository({required this.enabled, required this.hasStoredSession})
    : super(
        ApiClient(baseUrl: 'http://test', tokens: TokenStorage()),
        TokenStorage(),
      );

  final bool enabled;
  final bool hasStoredSession;
  var logoutCalls = 0;
  var meCalls = 0;

  @override
  Future<ServerAuthStatus> status() async =>
      ServerAuthStatus(enabled: enabled, configured: enabled);

  @override
  Future<bool> hasSession() async => hasStoredSession;

  @override
  Future<void> logout() async => logoutCalls++;

  @override
  Future<Account> me() async {
    meCalls++;
    return const Account(
      userId: 'local-owner',
      email: 'admin@local.vidtrans',
      role: 'owner',
    );
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() => FlutterSecureStorage.setMockInitialValues({}));

  test('authentication-disabled server starts a local owner session', () async {
    final auth = _FakeAuthRepository(enabled: false, hasStoredSession: false);
    final container = ProviderContainer(
      overrides: [authRepositoryProvider.overrideWithValue(auth)],
    );
    addTearDown(container.dispose);

    final account = await container.read(sessionProvider.future);

    expect(account?.role, 'owner');
    expect(container.read(authEnabledProvider), isFalse);
    expect(auth.logoutCalls, 1);
    expect(auth.meCalls, 1);
  });

  test(
    'authentication-enabled server without tokens stays signed out',
    () async {
      final auth = _FakeAuthRepository(enabled: true, hasStoredSession: false);
      final container = ProviderContainer(
        overrides: [authRepositoryProvider.overrideWithValue(auth)],
      );
      addTearDown(container.dispose);

      expect(await container.read(sessionProvider.future), isNull);
      expect(container.read(authEnabledProvider), isTrue);
      expect(auth.meCalls, 0);
    },
  );
}
