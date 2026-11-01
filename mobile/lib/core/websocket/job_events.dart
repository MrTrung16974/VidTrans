import 'dart:async';
import 'dart:math';

import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;

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

/// `notification` event: a job finished or failed (same content as the FCM push).
class JobNotification {
  const JobNotification({required this.jobId, required this.status, this.title, this.body});

  final String jobId;
  final String status;
  final String? title;
  final String? body;

  static JobNotification? fromJson(Object? json) {
    if (json is! Map || json['type'] != 'job_status') return null;
    final jobId = json['job_id']?.toString() ?? '';
    if (jobId.isEmpty) return null;
    return JobNotification(
      jobId: jobId,
      status: json['status']?.toString() ?? '',
      title: json['title'] as String?,
      body: json['body'] as String?,
    );
  }
}

final jobEventsProvider = Provider<JobEventsService>((ref) {
  final service = JobEventsService(ref.watch(apiClientProvider));
  ref.onDispose(service.dispose);
  return service;
});

/// Socket.IO connection to `/api/v1/socket.io`: job progress (`jobs`) and
/// in-app notifications (`notification`). Closed while the app is in the
/// background (FCM covers that); every reconnect emits a resync because REST
/// stays the source of truth for job state.
///
/// Reconnecting is done here rather than by socket.io: a refused connection
/// (expired token) needs a token refresh first, and socket.io does not retry
/// those by itself.
class JobEventsService {
  JobEventsService(this._api) {
    _lifecycle = AppLifecycleListener(onResume: connect, onPause: disconnect);
  }

  static const socketPath = '/api/v1/socket.io';
  static const _maxBackoff = Duration(seconds: 30);
  static const _fallbackPoll = Duration(seconds: 15);

  final ApiClient _api;
  final _controller = StreamController<JobChanges>.broadcast();
  final _notifications = StreamController<JobNotification>.broadcast();
  late final AppLifecycleListener _lifecycle;

  io.Socket? _socket;
  Timer? _reconnectTimer;
  Timer? _pollTimer;
  int _attempts = 0;
  bool _hasConnectedBefore = false;
  bool _authExpired = false;
  bool _disposed = false;

  Stream<JobChanges> get changes {
    connect();
    return _controller.stream;
  }

  Stream<JobNotification> get notifications {
    connect();
    return _notifications.stream;
  }

  /// Connects when signed in; a no-op while connected or without tokens.
  Future<void> connect() async {
    if (_disposed || _socket != null) return;
    _reconnectTimer?.cancel();
    if (await _api.tokens.read() == null || _disposed || _socket != null) return;

    final socket = io.io(
      Env.apiBase,
      io.OptionBuilder()
          .setPath(socketPath)
          .setTransports(['websocket'])
          .disableAutoConnect()
          .disableReconnection()
          .enableForceNew()
          // Read on every connect, so a refreshed token is used.
          .setAuthFn((send) => _api.tokens.read().then((tokens) => send({'token': tokens?.accessToken ?? ''})))
          .build(),
    );
    _socket = socket;
    _authExpired = false;
    socket
      ..onConnect((_) => _onConnected())
      ..on('jobs', (data) {
        if (data is Map) _controller.add(JobChanges.fromJson(Map<String, dynamic>.from(data)));
      })
      ..on('notification', (data) {
        final notification = JobNotification.fromJson(data);
        if (notification != null) _notifications.add(notification);
      })
      ..on('auth_expired', (_) => _authExpired = true)
      ..onConnectError((error) => _onClosed(socket, unauthorized: _isUnauthorized(error)))
      ..onDisconnect((_) => _onClosed(socket, unauthorized: _authExpired));
    socket.connect();
  }

  void _onConnected() {
    _attempts = 0;
    _pollTimer?.cancel();
    _pollTimer = null;
    if (_hasConnectedBefore) _controller.add(const JobChanges(resync: true));
    _hasConnectedBefore = true;
  }

  static bool _isUnauthorized(Object? error) =>
      error == 'unauthorized' || (error is Map && error['message'] == 'unauthorized');

  Future<void> _onClosed(io.Socket socket, {required bool unauthorized}) async {
    if (_socket != socket) return;
    _socket = null;
    _release(socket);
    if (_disposed) return;
    if (unauthorized) {
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
    _reconnectTimer = Timer(delay, connect);
  }

  static void _release(io.Socket socket) {
    socket
      ..clearListeners()
      ..dispose();
  }

  /// Closes the connection (app in background, or signed out).
  void disconnect() {
    _reconnectTimer?.cancel();
    _pollTimer?.cancel();
    _pollTimer = null;
    final socket = _socket;
    _socket = null;
    if (socket != null) _release(socket);
  }

  void dispose() {
    _disposed = true;
    disconnect();
    _lifecycle.dispose();
    _controller.close();
    _notifications.close();
  }
}
