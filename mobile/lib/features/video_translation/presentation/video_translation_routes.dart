import 'package:go_router/go_router.dart';

import '../../image_translation/presentation/screens/image_translate_screen.dart';
import 'screens/translate_screen.dart';

/// Routes of this module; the root route is the tab in the bottom navigation.
final translationRoutes = <RouteBase>[
  GoRoute(
    path: '/translate',
    pageBuilder: (_, _) => const NoTransitionPage(child: TranslateScreen()),
    routes: [
      // Screenshot & live screen translation studio.
      GoRoute(
        path: 'image',
        pageBuilder: (_, state) => NoTransitionPage(
          child: ImageTranslateScreen(
            initialLiveMode: state.uri.queryParameters['mode'] == 'live',
          ),
        ),
      ),
    ],
  ),
];
