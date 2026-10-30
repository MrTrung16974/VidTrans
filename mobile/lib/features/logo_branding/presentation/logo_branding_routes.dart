import 'package:go_router/go_router.dart';

import 'screens/branding_job_screen.dart';
import 'screens/branding_screen.dart';
import 'screens/branding_settings_screen.dart';
import 'screens/branding_upload_screen.dart';

/// Routes of this module; the root route is the tab in the bottom navigation.
final brandingRoutes = <RouteBase>[
  GoRoute(
    path: '/branding',
    builder: (_, _) => const BrandingScreen(),
    routes: [
      // Static segments before ':jobId' so they are not taken for a job id.
      GoRoute(path: 'new', builder: (_, _) => const BrandingUploadScreen()),
      GoRoute(path: 'settings', builder: (_, _) => const BrandingSettingsScreen()),
      GoRoute(
        path: ':jobId',
        builder: (_, state) => BrandingJobScreen(jobId: state.pathParameters['jobId']!),
      ),
    ],
  ),
];
