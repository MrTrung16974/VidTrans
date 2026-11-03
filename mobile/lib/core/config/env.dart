/// API address. Override per build:
/// `flutter run --dart-define=VIDTRANS_API_BASE=https://vidtrans.cloud`
class Env {
  static const _override = String.fromEnvironment('VIDTRANS_API_BASE');

  static String get apiBase {
    if (_override.isNotEmpty) return _override;
    // Local development uses loopback on both Android and iOS. A physical
    // Android phone reaches the host after `adb reverse tcp:5210 tcp:5210`.
    // Android emulators can use the same reverse command, which keeps one
    // predictable default for every debug target. Production builds must pass
    // VIDTRANS_API_BASE=https://... explicitly.
    return 'http://127.0.0.1:5210';
  }

  static Uri wsUri(String path) {
    final base = Uri.parse(apiBase);
    return base.replace(
      scheme: base.scheme == 'https' ? 'wss' : 'ws',
      path: path,
    );
  }

  /// Deep link the backend redirects to after an OAuth flow started by the app
  /// (must be listed in VIDTRANS_APP_REDIRECT_URIS on the server).
  static const oauthRedirect = 'vidtrans://oauth';
}
