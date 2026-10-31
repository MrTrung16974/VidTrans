import 'package:go_router/go_router.dart';

import 'screens/branding_settings_screen.dart';

/// The logo and how it is stamped. Translation and comic forms choose per video
/// whether to stamp it ("Chèn logo"); there is no separate upload-to-brand flow.
final brandingRoutes = <RouteBase>[
  GoRoute(path: '/branding', builder: (_, _) => const BrandingSettingsScreen()),
];
