import 'package:go_router/go_router.dart';

import 'screens/translate_screen.dart';

/// Routes of this module; the root route is the tab in the bottom navigation.
final translationRoutes = <RouteBase>[
  GoRoute(path: '/translate', builder: (_, _) => const TranslateScreen()),
];
