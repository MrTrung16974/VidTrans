import 'package:go_router/go_router.dart';

import 'screens/comic_home_screen.dart';
import 'screens/create_episode_screen.dart';
import 'screens/create_story_screen.dart';
import 'screens/project_editor_screen.dart';
import 'screens/scene_editor_screen.dart';
import 'screens/series_detail_screen.dart';

/// Routes of this module; the root route is the tab in the bottom navigation.
final comicRoutes = <RouteBase>[
  GoRoute(
    path: '/comic',
    builder: (_, _) => const ComicHomeScreen(),
    routes: [
      GoRoute(path: 'new', builder: (_, _) => const CreateStoryScreen()),
      GoRoute(
        path: 'series/:seriesId',
        builder: (_, state) => SeriesDetailScreen(seriesId: state.pathParameters['seriesId']!),
        routes: [
          GoRoute(
            path: 'episode',
            builder: (_, state) => CreateEpisodeScreen(seriesId: state.pathParameters['seriesId']!),
          ),
        ],
      ),
      GoRoute(
        path: 'projects/:jobId',
        builder: (_, state) => ProjectEditorScreen(jobId: state.pathParameters['jobId']!),
        routes: [
          GoRoute(
            path: 'scenes/:sceneIndex',
            builder: (_, state) => SceneEditorScreen(
              jobId: state.pathParameters['jobId']!,
              sceneIndex: int.tryParse(state.pathParameters['sceneIndex']!) ?? 1,
            ),
          ),
        ],
      ),
    ],
  ),
];
