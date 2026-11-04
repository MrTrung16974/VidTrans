import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/auth/session_controller.dart';
import '../core/deeplink/deep_link_service.dart';
import '../core/push/push_service.dart';
import '../core/websocket/job_events.dart';
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

  /// Job outcomes already shown: the Socket.IO event and the FCM push of the
  /// same outcome usually both arrive while the app is open.
  final _shownOutcomes = <String>{};

  @override
  void initState() {
    super.initState();
    final push = ref.read(pushServiceProvider);
    final jobEvents = ref.read(jobEventsProvider);
    _subscriptions
      ..add(push.opened.listen((event) => _openJob(event.jobId)))
      ..add(push.foreground.listen((p) => _showJobOutcome(p.jobId, p.status, p.title, p.body)))
      ..add(jobEvents.notifications.listen(
            (n) => _showJobOutcome(n.jobId, n.status, n.title, n.body),
          ))
      ..add(jobEvents.changes.listen(_onJobChanges))
      ..add(ref.read(deepLinkServiceProvider).links.listen(_onDeepLink));
    // Listen first: initialize() replays the push that launched the app.
    unawaited(push.initialize());

    ref.listenManual<AsyncValue<dynamic>>(sessionProvider, (previous, next) {
      final account = next.valueOrNull;
      final events = ref.read(jobEventsProvider);
      if (account != null && previous?.valueOrNull?.userId != account.userId) {
        unawaited(push.onSignedIn());
        // Reconnect so the socket carries the new account's token.
        events.disconnect();
        unawaited(events.connect());
      } else if (account == null) {
        events.disconnect();
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

  void _onJobChanges(JobChanges changes) {
    for (final item in changes.items) {
      final status = item['status'] as String?;
      final jobId = item['job_id']?.toString() ?? item['id']?.toString();
      if (status == 'completed' && jobId != null) {
        final title = item['filename'] as String? ??
            item['source_url'] as String? ??
            item['title'] as String? ??
            'Video';
        _showJobOutcome(
          jobId,
          status,
          'Xử lý video thành công! 🎉',
          '"$title" đã hoàn tất. Bấm để xem và tải video.',
        );
      }
    }
  }

  void _showJobOutcome(String jobId, String status, String? title, String? body) {
    if (!_shownOutcomes.add('$jobId:$status')) return;
    // A retried job can finish again later.
    Timer(const Duration(minutes: 2), () => _shownOutcomes.remove('$jobId:$status'));

    final isSuccess = status == 'completed';
    final isFailure = status == 'failed';

    // Provide tactile feedback on completion
    if (isSuccess) {
      HapticFeedback.mediumImpact();
    } else if (isFailure) {
      HapticFeedback.heavyImpact();
    }

    final messenger = scaffoldMessengerKey.currentState;
    if (messenger == null) return;

    final headerText = title ??
        (isSuccess ? 'Xử lý video thành công! 🎉' : (isFailure ? 'Xử lý video thất bại' : 'Tiến trình cập nhật'));
    final detailText = body ?? (isSuccess ? 'Video đã sẵn sàng để xem và xuất.' : null);

    messenger.hideCurrentSnackBar();
    messenger.showSnackBar(
      SnackBar(
        behavior: SnackBarBehavior.floating,
        margin: const EdgeInsets.fromLTRB(14, 0, 14, 20),
        padding: const EdgeInsets.fromLTRB(14, 11, 10, 11),
        backgroundColor: const Color(0xFF0F172A),
        elevation: 10,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: BorderSide(
            color: isSuccess
                ? const Color(0x6622C55E)
                : (isFailure ? const Color(0x66EF4444) : const Color(0x33CBD5E1)),
            width: 1.2,
          ),
        ),
        duration: const Duration(seconds: 5),
        content: Row(
          children: [
            Container(
              width: 36,
              height: 36,
              decoration: BoxDecoration(
                color: isSuccess
                    ? const Color(0x2E22C55E)
                    : (isFailure ? const Color(0x2EEF4444) : const Color(0x2E0EA5E9)),
                shape: BoxShape.circle,
              ),
              alignment: Alignment.center,
              child: Icon(
                isSuccess
                    ? Icons.check_circle_rounded
                    : (isFailure ? Icons.error_outline_rounded : Icons.info_outline_rounded),
                color: isSuccess
                    ? const Color(0xFF22C55E)
                    : (isFailure ? const Color(0xFFEF4444) : const Color(0xFF38BDF8)),
                size: 22,
              ),
            ),
            const SizedBox(width: 11),
            Expanded(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    headerText,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w800,
                      color: Colors.white,
                      letterSpacing: -0.1,
                    ),
                  ),
                  if (detailText != null && detailText.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(
                      detailText,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 11.2,
                        fontWeight: FontWeight.w500,
                        color: Color(0xFF94A3B8),
                        height: 1.25,
                      ),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(width: 8),
            DecoratedBox(
              decoration: BoxDecoration(
                color: const Color(0xFFEA580C),
                gradient: const LinearGradient(
                  colors: [Color(0xFFF97316), Color(0xFFEA580C)],
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                ),
                borderRadius: BorderRadius.circular(999),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x59EA580C),
                    blurRadius: 8,
                    offset: Offset(0, 2),
                  ),
                ],
              ),
              child: Material(
                color: Colors.transparent,
                child: InkWell(
                  borderRadius: BorderRadius.circular(999),
                  onTap: () {
                    messenger.hideCurrentSnackBar();
                    _openJob(jobId);
                  },
                  child: const Padding(
                    padding: EdgeInsets.symmetric(horizontal: 13, vertical: 7),
                    child: Text(
                      'Xem',
                      style: TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.w800,
                        fontSize: 12,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
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
