import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/auth/session_controller.dart';
import '../core/deeplink/deep_link_service.dart';
import '../core/push/push_service.dart';
import 'router.dart';
import 'theme/app_theme.dart';

final scaffoldMessengerKey = GlobalKey<ScaffoldMessengerState>();

class VidTransApp extends ConsumerStatefulWidget {
  const VidTransApp({super.key});

  @override
  ConsumerState<VidTransApp> createState() => _VidTransAppState();
}

class _VidTransAppState extends ConsumerState<VidTransApp> {
  final _subscriptions = <StreamSubscription<dynamic>>[];

  @override
  void initState() {
    super.initState();
    final push = ref.read(pushServiceProvider);
    _subscriptions
      ..add(push.opened.listen((event) => _openJob(event.jobId)))
      ..add(push.foreground.listen(_showForegroundPush))
      ..add(ref.read(deepLinkServiceProvider).links.listen(_onDeepLink));
    // Listen first: initialize() replays the push that launched the app.
    unawaited(push.initialize());

    ref.listenManual<AsyncValue<dynamic>>(sessionProvider, (previous, next) {
      final account = next.valueOrNull;
      if (account != null && previous?.valueOrNull?.userId != account.userId) {
        unawaited(push.onSignedIn());
      }
    }, fireImmediately: true);
  }

  @override
  void dispose() {
    for (final subscription in _subscriptions) {
      subscription.cancel();
    }
    super.dispose();
  }

  void _openJob(String jobId) => ref.read(routerProvider).push('/jobs/$jobId');

  void _showForegroundPush(JobPush push) {
    scaffoldMessengerKey.currentState?.showSnackBar(SnackBar(
      content: Text(push.body ?? push.title ?? 'Công việc đã cập nhật'),
      action: SnackBarAction(
        label: 'Xem',
        onPressed: () => _openJob(push.jobId),
      ),
    ));
  }

  void _onDeepLink(DeepLink link) {
    switch (link) {
      case JobLink(:final jobId):
        _openJob(jobId);
      case OAuthResult(:final platform, :final error):
        final name = platform.isEmpty
            ? platform
            : platform[0].toUpperCase() + platform.substring(1);
        scaffoldMessengerKey.currentState?.showSnackBar(SnackBar(
          content: Text(
            error == null
                ? 'Đã kết nối $name'
                : 'Kết nối $name thất bại: $error',
          ),
        ));
    }
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'VidTrans Creator Studio',
      debugShowCheckedModeBanner: false,
      scaffoldMessengerKey: scaffoldMessengerKey,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      themeMode: ThemeMode.light,
      routerConfig: ref.watch(routerProvider),
    );
  }
}
