import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../core/auth/session_controller.dart';
import '../core/widgets/studio_widgets.dart';
import '../features/authentication/presentation/screens/login_screen.dart';
import '../features/comic_video/presentation/comic_video_routes.dart';
import '../features/home/presentation/screens/home_shell.dart';
import '../features/jobs/presentation/screens/job_detail_screen.dart';
import '../features/jobs/presentation/screens/jobs_screen.dart';
import '../features/jobs/presentation/screens/video_player_screen.dart';
import '../features/logo_branding/presentation/logo_branding_routes.dart';
import '../features/profile/presentation/screens/profile_screen.dart';
import '../features/social_publishing/presentation/social_publishing_routes.dart';
import '../features/video_translation/presentation/video_translation_routes.dart';
import 'theme/app_theme.dart';

final routerProvider = Provider<GoRouter>((ref) {
  // Re-run redirects whenever the session changes, without rebuilding the router.
  final sessionChanges = ValueNotifier(0);
  ref.listen(sessionProvider, (_, _) => sessionChanges.value++);
  ref.onDispose(sessionChanges.dispose);

  return GoRouter(
    initialLocation: '/translate',
    refreshListenable: sessionChanges,
    redirect: (context, state) {
      final session = ref.read(sessionProvider);
      final location = state.matchedLocation;
      if (session.isLoading && !session.hasValue) {
        return location == '/splash' ? null : '/splash';
      }
      final signedIn = session.valueOrNull != null;
      if (!signedIn) return location == '/login' ? null : '/login';
      if (location == '/login' || location == '/splash') return '/translate';
      return null;
    },
    routes: [
      GoRoute(
        path: '/splash',
        builder: (_, _) => const Scaffold(
          backgroundColor: AppColors.bg,
          body: Center(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                VidTransLogo(size: 64),
                SizedBox(height: 14),
                Text(
                  'VidTrans',
                  style: TextStyle(
                    fontSize: 22,
                    fontWeight: FontWeight.w800,
                    color: AppColors.ink,
                    letterSpacing: -0.5,
                  ),
                ),
                SizedBox(height: 2),
                Text(
                  'CREATOR STUDIO',
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    color: AppColors.muted,
                    letterSpacing: 1.2,
                  ),
                ),
                SizedBox(height: 24),
                SizedBox(
                  width: 24,
                  height: 24,
                  child: CircularProgressIndicator(
                    strokeWidth: 2.5,
                    color: AppColors.brand,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
      GoRoute(path: '/login', builder: (_, _) => const LoginScreen()),
      // Opened from the account menu: full screen above the bottom navigation.
      ...brandingRoutes,
      StatefulShellRoute.indexedStack(
        builder: (_, _, shell) => HomeShell(shell: shell),
        branches: [
          StatefulShellBranch(routes: translationRoutes),
          StatefulShellBranch(routes: comicRoutes),
          StatefulShellBranch(routes: [
            GoRoute(
              path: '/jobs',
              builder: (_, _) => const JobsScreen(),
              routes: [
                GoRoute(
                  path: ':jobId',
                  builder: (_, state) =>
                      JobDetailScreen(jobId: state.pathParameters['jobId']!),
                  routes: [
                    GoRoute(
                      path: 'player',
                      builder: (_, state) => VideoPlayerScreen(
                        jobId: state.pathParameters['jobId']!,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ]),
          StatefulShellBranch(routes: socialRoutes),
          StatefulShellBranch(routes: [
            GoRoute(
              path: '/profile',
              builder: (_, _) => const ProfileScreen(),
            ),
          ]),
        ],
      ),
    ],
  );
});
