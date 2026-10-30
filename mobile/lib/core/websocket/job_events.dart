import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:web_socket_channel/io.dart';

import '../api/api_client.dart';
import '../api/providers.dart';
import '../config/env.dart';

/// Change set pushed by `/api/v1/jobs/events` (same format as web/js/job-events.js).
class JobChanges {
  const JobChanges({
    this.items = const [],
    this.removed = const [],
    this.queuePositions,
    this.resync = false,
  });

  final List<Map<String, dynamic>> items;
  final List<String> removed;
  final Map<String, int>? queuePositions;

  /// The client may have missed events: reload from REST.
  final bool resync;

  factory JobChanges.fromJson(Map<String, dynamic> json) => JobChanges(
        items: [
          for (final item in (json['items'] as List? ?? const []))
            if (item is Map) Map<String, dynamic>.from(item),
        ],
        removed: [for (final id in (json['removed'] as List? ?? const [])) id.toString()],
        queuePositions: (json['queue_positions'] as Map?)
            ?.map((key, value) => MapEntry(key.toString(), (value as num).toInt())),
        resync: json['resync'] == true,
      );
}

final jobEventsProvider = Provider<JobEventsService>((ref) {
  final service = JobEventsService(ref.watch(apiClientProvider));
  ref.onDispose(service.dispose);
  return service;
});

/// One socket with exponential reconnect backoff. Closed while the app is in
/// the background; every reconnect emits a resync because REST stays the
/// source of truth for job state.
class JobEventsService {
  JobEventsService(this._api) {
    _lifecycle = AppLifecycleListener(onResume: _connect, onPause: _disconnect);
  }

  static const _unauthorizedCloseCode = 4401;
  static const _maxBackoff = Duration(seconds: 30);
  static const _fallbackPoll = Duration(seconds: 15);

  final ApiClient _api;
  final _controller = StreamController<JobChanges>.broadcast();
  late final AppLifecycleListener _lifecycle;

  IOWebSocketChannel? _channel;
  StreamSubscription<dynamic>? _subscription;
  Timer? _reconnectTimer;
  Timer? _pollTimer;
  int _attempts = 0;
  bool _hasConnectedBefore = false;
  bool _disposed = false;

  Stream<JobChanges> get changes {
    _connect();
    return _controller.stream;
  }

  Future<void> _connect() async {
    if (_disposed || _channel != null) return;
    _reconnectTimer?.cancel();
    final tokens = await _api.tokens.read();
    if (_disposed || tokens == null || _channel != null) return;

    final channel = IOWebSocketChannel.connect(
      Env.wsUri('/api/v1/jobs/events'),
      headers: {'Authorization': 'Bearer ${tokens.accessToken}'},
      pingInterval: const Duration(seconds: 25),
    );
    _channel = channel;
    try {
      await channel.ready;
    } catch (_) {
      // A rejected handshake (HTTP 403) usually means an expired token.
      if (_channel == channel) await _onClosed(_unauthorizedCloseCode);
      return;
    }
    _subscription = channel.stream.listen(
      _onMessage,
      onDone: () => _onClosed(channel.closeCode),
      onError: (_) => _onClosed(null),
      cancelOnError: true,
    );
  }

  void _onMessage(dynamic raw) {
    final Object? message;
    try {
      message = jsonDecode(raw as String);
    } catch (_) {
      return;
    }
    if (message is! Map<String, dynamic>) return;
    switch (message['type']) {
      case 'ready':
        _attempts = 0;
        _pollTimer?.cancel();
        _pollTimer = null;
        if (_hasConnectedBefore) _controller.add(const JobChanges(resync: true));
        _hasConnectedBefore = true;
      case 'jobs':
        _controller.add(JobChanges.fromJson(message));
    }
  }

  Future<void> _onClosed(int? code) async {
    _subscription = null;
    _channel = null;
    if (_disposed) return;
    if (code == _unauthorizedCloseCode) {
      try {
        if (await _api.refreshAccessToken() == null) return; // signed out
      } catch (_) {
        // Network error during refresh: fall through to backoff.
      }
    }
    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (_disposed) return;
    // While the socket is down, a slow REST reload keeps screens fresh.
    _pollTimer ??= Timer.periodic(_fallbackPoll, (_) => _controller.add(const JobChanges(resync: true)));
    final delay = Duration(seconds: min(_maxBackoff.inSeconds, pow(2, _attempts).toInt()));
    _attempts += 1;
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(delay, _connect);
  }

  void _disconnect() {
    _reconnectTimer?.cancel();
    _pollTimer?.cancel();
    _pollTimer = null;
    final channel = _channel;
    _channel = null;
    _subscription?.cancel();
    _subscription = null;
    channel?.sink.close();
  }

  void dispose() {
    _disposed = true;
    _disconnect();
    _lifecycle.dispose();
    _controller.close();
  }
}
