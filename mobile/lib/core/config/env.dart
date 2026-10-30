import 'dart:io';

/// API address. Override per build:
/// `flutter run --dart-define=VIDTRANS_API_BASE=https://vidtrans.cloud`
class Env {
  static const _override = String.fromEnvironment('VIDTRANS_API_BASE');

  static String get apiBase {
    if (_override.isNotEmpty) return _override;
    // Local Docker backend (docs/mobile-api.md): the Android emulator reaches the
    // host through 10.0.2.2, the iOS simulator through loopback.
    return Platform.isAndroid ? 'http://10.0.2.2:5210' : 'http://127.0.0.1:5210';
  }

  static Uri wsUri(String path) {
    final base = Uri.parse(apiBase);
    return base.replace(scheme: base.scheme == 'https' ? 'wss' : 'ws', path: path);
  }

  /// Deep link the backend redirects to after an OAuth flow started by the app
  /// (must be listed in VIDTRANS_APP_REDIRECT_URIS on the server).
  static const oauthRedirect = 'vidtrans://oauth';
}
