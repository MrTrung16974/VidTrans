import 'dart:io';

import 'package:firebase_core/firebase_core.dart';

/// Firebase options from `--dart-define`, so no google-services.json or
/// GoogleService-Info.plist is committed. Missing values disable push.
///
/// flutter run \
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
}
