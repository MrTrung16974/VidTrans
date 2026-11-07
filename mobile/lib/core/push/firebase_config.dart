import 'dart:io';

import 'package:firebase_core/firebase_core.dart';

/// Where Firebase reads its project settings.
///
/// Android: `android/app/google-services.json` (project `vistrans-e0fbc`), turned
/// into resources by the `com.google.gms.google-services` Gradle plugin, so
/// `Firebase.initializeApp()` needs no options.
///
/// iOS (no GoogleService-Info.plist yet) and overrides: `--dart-define`
///   --dart-define=FIREBASE_API_KEY=... --dart-define=FIREBASE_PROJECT_ID=... \
///   --dart-define=FIREBASE_MESSAGING_SENDER_ID=... \
///   --dart-define=FIREBASE_ANDROID_APP_ID=1:...:android:... \
///   --dart-define=FIREBASE_IOS_APP_ID=1:...:ios:...
class FirebaseConfig {
  static const _apiKey = String.fromEnvironment('FIREBASE_API_KEY');
  static const _projectId = String.fromEnvironment('FIREBASE_PROJECT_ID');
  static const _senderId = String.fromEnvironment('FIREBASE_MESSAGING_SENDER_ID');
  static const _androidAppId = String.fromEnvironment('FIREBASE_ANDROID_APP_ID');
  static const _iosAppId = String.fromEnvironment('FIREBASE_IOS_APP_ID');
  static const _iosBundleId = String.fromEnvironment('FIREBASE_IOS_BUNDLE_ID', defaultValue: 'cloud.vidtrans.app');

  /// Options from `--dart-define`, or null when they are not all set.
  static FirebaseOptions? get current {
    final appId = Platform.isIOS ? _iosAppId : _androidAppId;
    if (_apiKey.isEmpty || _projectId.isEmpty || _senderId.isEmpty || appId.isEmpty) return null;
    return FirebaseOptions(
      apiKey: _apiKey,
      appId: appId,
      messagingSenderId: _senderId,
      projectId: _projectId,
      iosBundleId: Platform.isIOS ? _iosBundleId : null,
    );
  }

  /// True when the platform carries its own Firebase config file (Android:
  /// google-services.json), so push works without dart-defines.
  static bool get hasNativeConfig => Platform.isAndroid;

  /// Push can start: either dart-defines or the bundled config file.
  static bool get available => current != null || hasNativeConfig;
}
