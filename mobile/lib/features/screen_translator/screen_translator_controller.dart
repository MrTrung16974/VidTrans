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

  /// Asks Android for screen capture consent (every session on Android 14+),
  /// then starts the service. False when the user declined.
  Future<bool> start({
    required String source,
    required String target,
    required String mode,
  }) async =>
      await _channel.invokeMethod<bool>('start', {
        'source': source,
        'target': target,
        'mode': mode,
      }) ??
      false;

  Future<void> updateSettings({
    required String source,
    required String target,
    required String mode,
  }) => _channel.invokeMethod<void>('updateSettings', {
    'source': source,
    'target': target,
    'mode': mode,
  });

  Future<void> stop() => _channel.invokeMethod<void>('stop');
}

final screenTranslatorControllerProvider = Provider<ScreenTranslatorController>(
  (ref) => const ScreenTranslatorController(),
);
