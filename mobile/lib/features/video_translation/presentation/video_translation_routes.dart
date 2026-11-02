import 'package:go_router/go_router.dart';

import '../../image_translation/presentation/screens/image_translate_screen.dart';
import 'screens/translate_screen.dart';

/// Routes of this module; the root route is the tab in the bottom navigation.
final translationRoutes = <RouteBase>[
  GoRoute(
    path: '/translate',
    builder: (_, _) => const TranslateScreen(),
    routes: [
      // Screenshot translation (phase 1 of the screen translator).
      GoRoute(path: 'image', builder: (_, _) => const ImageTranslateScreen()),
    ],
  ),
];
