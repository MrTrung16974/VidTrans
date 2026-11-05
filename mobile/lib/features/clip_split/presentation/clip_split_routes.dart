import 'package:go_router/go_router.dart';

import 'screens/clip_split_screen.dart';

/// "Cắt clip" opens full screen above the dock (a feature of its own, not a dock tab).
/// `/clips?source=<job id>` starts on that finished video's plan.
final clipSplitRoutes = <RouteBase>[
  GoRoute(
    path: '/clips',
    builder: (_, state) => ClipSplitScreen(sourceJobId: state.uri.queryParameters['source']),
  ),
];
