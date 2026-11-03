import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// App side of the floating screen translator (Android only):
/// permissions, start/stop and settings of ScreenTranslatorService.
class ScreenTranslatorController {
  const ScreenTranslatorController([
    this._channel = const MethodChannel('vidtrans/screen_translator'),
  ]);

  final MethodChannel _channel;

  bool get supported => !kIsWeb && Platform.isAndroid;

  /// "Display over other apps" (SYSTEM_ALERT_WINDOW).
  Future<bool> canDrawOverlays() async =>
      await _channel.invokeMethod<bool>('canDrawOverlays') ?? false;

  Future<void> openOverlaySettings() =>
      _channel.invokeMethod<void>('openOverlaySettings');

  Future<bool> isRunning() async =>
      await _channel.invokeMethod<bool>('isRunning') ?? false;

  /// Live (continuous) translation currently on; the floating button's long
  /// press can change it without the app.
  Future<bool> isLive() async =>
      await _channel.invokeMethod<bool>('isLive') ?? false;

  /// Asks Android for screen capture consent (every session on Android 14+),
  /// then starts the service. False when the user declined.
  Future<bool> start({
    required String source,
    required String target,
    required String mode,
    bool live = false,
  }) async =>
      await _channel.invokeMethod<bool>('start', {
        'source': source,
        'target': target,
        'mode': mode,
        'live': live,
      }) ??
      false;

  Future<void> updateSettings({
    required String source,
    required String target,
    required String mode,
    bool live = false,
  }) => _channel.invokeMethod<void>('updateSettings', {
    'source': source,
    'target': target,
    'mode': mode,
    'live': live,
  });

  Future<void> stop() => _channel.invokeMethod<void>('stop');

  // Accessibility mode (AccessibilityTranslatorService): the text comes from
  // the app's view tree, no screen capture consent and no OCR.

  /// The user switched VidTrans on in Settings › Accessibility.
  Future<bool> a11yEnabledInSettings() async =>
      await _channel.invokeMethod<bool>('a11yEnabled') ?? false;

  Future<void> openAccessibilitySettings() =>
      _channel.invokeMethod<void>('openAccessibilitySettings');

  /// Whether the app asked the service to translate (it may still be off in
  /// the system settings).
  Future<bool> a11yTranslating() async {
    final config = await _channel.invokeMapMethod<String, Object?>(
      'a11yConfig',
    );
    return config?['enabled'] == true;
  }

  /// Turns translating on/off and/or hands over the languages; the service
  /// picks the change up at once.
  Future<void> setA11yConfig({
    bool? enabled,
    required String source,
    required String target,
    required String mode,
  }) => _channel.invokeMethod<void>('setA11yConfig', {
    'enabled': ?enabled,
    'source': source,
    'target': target,
    'mode': mode,
  });
}

final screenTranslatorControllerProvider = Provider<ScreenTranslatorController>(
  (ref) => const ScreenTranslatorController(),
);
