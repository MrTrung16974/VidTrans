import 'dart:async';
import 'dart:io';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/api_client.dart';
import '../api/providers.dart';
import 'firebase_config.dart';

/// A job push (`data: {type: job_status, job_id, status}`).
class JobPush {
  const JobPush({required this.jobId, required this.status, this.title, this.body});

  final String jobId;
  final String status;
  final String? title;
  final String? body;

  static JobPush? fromMessage(RemoteMessage message) {
    final data = message.data;
    final jobId = data['job_id']?.toString();
    if (data['type'] != 'job_status' || jobId == null || jobId.isEmpty) return null;
    return JobPush(
      jobId: jobId,
      status: data['status']?.toString() ?? '',
      title: message.notification?.title,
      body: message.notification?.body,
    );
  }
}

final pushServiceProvider = Provider<PushService>((ref) {
  final service = PushService(ref.watch(apiClientProvider));
  ref.onDispose(service.dispose);
  return service;
});

/// Firebase Cloud Messaging: registers this phone with `/api/v1/me/devices`
/// while signed in and reports pushes. Without Firebase config (see
/// [FirebaseConfig]) every method is a no-op.
class PushService {
  PushService(this._api);

  static const _deviceIdKey = 'vidtrans.push.device_id';

  final ApiClient _api;
  final _opened = StreamController<JobPush>.broadcast();
  final _foreground = StreamController<JobPush>.broadcast();
  final _subscriptions = <StreamSubscription<dynamic>>[];
  Future<bool>? _initializing;
  bool _signedIn = false;

  /// Pushes the user tapped (app in background, or launched from it).
  Stream<JobPush> get opened => _opened.stream;

  /// Pushes received while the app is on screen (the OS shows nothing then).
  Stream<JobPush> get foreground => _foreground.stream;

  Future<bool> initialize() => _initializing ??= _initialize();

  Future<bool> _initialize() async {
    final options = FirebaseConfig.current;
    if (options == null) {
      debugPrint('Push disabled: FIREBASE_* dart-defines are not set');
      return false;
    }
    try {
      if (Firebase.apps.isEmpty) await Firebase.initializeApp(options: options);
      final messaging = FirebaseMessaging.instance;
      _subscriptions
        ..add(FirebaseMessaging.onMessageOpenedApp.listen(_onOpened))
        ..add(FirebaseMessaging.onMessage.listen((message) {
          final push = JobPush.fromMessage(message);
          if (push != null) _foreground.add(push);
        }))
        // FCM HTTP v1 on the server sends to registration tokens; the newer
        // register()/onRegistered API yields installation IDs instead.
        // ignore: deprecated_member_use
        ..add(messaging.onTokenRefresh.listen((token) {
          if (_signedIn) unawaited(_register(token));
        }));
      final initial = await messaging.getInitialMessage();
      if (initial != null) _onOpened(initial);
      return true;
    } catch (error) {
      debugPrint('Push disabled: $error');
      return false;
    }
  }

  void _onOpened(RemoteMessage message) {
    final push = JobPush.fromMessage(message);
    if (push != null) _opened.add(push);
  }

  /// Call after sign-in. Asks for permission once, then registers the token.
  Future<void> onSignedIn() async {
    _signedIn = true;
    if (!await initialize()) return;
    try {
      final messaging = FirebaseMessaging.instance;
      final settings = await messaging.requestPermission();
      if (settings.authorizationStatus == AuthorizationStatus.denied) return;
      if (Platform.isIOS && await messaging.getAPNSToken() == null) {
        // The APNs token arrives shortly after the first launch; onTokenRefresh
        // registers it when it does.
        return;
      }
      // ignore: deprecated_member_use
      final token = await messaging.getToken();
      if (token != null) await _register(token);
    } catch (error) {
      debugPrint('Push registration failed: $error');
    }
  }

  Future<void> _register(String token) async {
    final info = await PackageInfo.fromPlatform();
    final device = await _api.post<Map<String, dynamic>>('/api/v1/me/devices', data: {
      'token': token,
      'platform': Platform.isIOS ? 'ios' : 'android',
      'app_version': '${info.version}+${info.buildNumber}',
    });
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_deviceIdKey, device['device_id'] as String);
  }

  /// Call before the tokens are cleared, so the request is still authorised.
  Future<void> onSigningOut() async {
    _signedIn = false;
    final prefs = await SharedPreferences.getInstance();
    final deviceId = prefs.getString(_deviceIdKey);
    if (deviceId == null) return;
    await prefs.remove(_deviceIdKey);
    try {
      await _api.delete<dynamic>('/api/v1/me/devices/$deviceId');
    } catch (error) {
      // Offline sign-out: the server drops the token once FCM reports it dead
      // or another account registers it.
      debugPrint('Push unregistration failed: $error');
    }
  }

  void dispose() {
    for (final subscription in _subscriptions) {
      subscription.cancel();
    }
    _opened.close();
    _foreground.close();
  }
}
