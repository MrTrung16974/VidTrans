import 'package:go_router/go_router.dart';

import 'screens/social_hub_screen.dart';
import 'screens/social_publish_screen.dart';

/// "Đăng MXH" tab of the bottom navigation.
final socialRoutes = <RouteBase>[
  GoRoute(
    path: '/social',
    builder: (_, _) => const SocialHubScreen(),
    routes: [
      GoRoute(
        path: 'publish/:jobId',
        builder: (_, state) => SocialPublishScreen(jobId: state.pathParameters['jobId']!),
      ),
    ],
  ),
];
