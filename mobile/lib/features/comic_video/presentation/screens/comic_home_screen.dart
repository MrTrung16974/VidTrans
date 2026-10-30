import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../../home/presentation/screens/home_shell.dart';
import '../../../jobs/domain/job.dart';
import '../../data/comic_repository.dart';
import '../../domain/comic_labels.dart';
import '../../domain/comic_models.dart';
import '../providers/comic_providers.dart';
import '../widgets/comic_media.dart';

/// Tab "Truyện tranh": stories (series) and every episode/video.
class ComicHomeScreen extends ConsumerWidget {
  const ComicHomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final readOnly = comicReadOnly(ref);
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: const StudioTopBar(
          bottom: TabBar(
            tabs: [
              Tab(text: 'Truyện'),
              Tab(text: 'Video'),
            ],
          ),
        ),
        floatingActionButton: readOnly
            ? null
            : FloatingActionButton.extended(
                onPressed: () => context.push('/comic/new'),
                icon: const Icon(Icons.add),
                label: const Text('Tạo truyện mới'),
              ),
        body: const TabBarView(children: [_SeriesTab(), _VideosTab()]),
      ),
    );
  }
}

class _SeriesTab extends ConsumerWidget {
  const _SeriesTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final series = ref.watch(comicSeriesListProvider);
    final options = ref.watch(comicOptionsProvider).valueOrNull;
    return series.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (error, _) =>
          ErrorView(message: errorMessage(error), onRetry: () => ref.invalidate(comicSeriesListProvider)),
      data: (items) => RefreshIndicator(
        onRefresh: () => ref.refresh(comicSeriesListProvider.future),
        child: items.isEmpty
            ? ListView(
                padding: const EdgeInsets.all(24),
                children: const [
                  SizedBox(height: 60),
                  StudioEmptyState(
                    icon: Icons.auto_stories_rounded,
                    title: 'Chưa có truyện nào. Bấm "Tạo truyện mới" để bắt đầu.',
                    subtitle: 'AI tự động thiết kế nhân vật, bối cảnh, chia cảnh và dựng video có lồng tiếng.',
                  ),
                ],
              )
            : ListView.separated(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
                itemCount: items.length,
                separatorBuilder: (_, _) => const SizedBox(height: 10),
                itemBuilder: (context, index) {
                  final item = items[index];
                  final meta = [
                    '${item.episodes} tập',
                    if (options != null && item.style != null) options.styleLabel(item.style),
                    item.videoMode == 'animated' ? 'Hoạt hình AI' : 'Truyện tranh',
                    ?item.aspectRatio,
                  ].join(' · ');
                  final isAnimated = item.videoMode == 'animated';
                  return StudioCard(
                    padding: EdgeInsets.zero,
                    child: ListTile(
                      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
                      leading: Container(
                        width: 44,
                        height: 44,
                        decoration: BoxDecoration(
                          color: isAnimated ? AppColors.secondarySoft : AppColors.primarySoft,
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: Icon(
                          isAnimated ? Icons.movie_filter_rounded : Icons.auto_stories_rounded,
                          color: isAnimated ? AppColors.secondary : AppColors.primary,
                        ),
                      ),
                      title: Text(
                        item.title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontWeight: FontWeight.w700),
                      ),
                      subtitle: Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Text(meta, style: const TextStyle(fontSize: 12, color: AppColors.textSecondary)),
                      ),
                      trailing: const Icon(Icons.chevron_right_rounded, color: AppColors.textMuted),
                      onTap: () => context.push('/comic/series/${item.seriesId}'),
                    ),
                  );
                },
              ),
      ),
    );
  }
}

class _VideosTab extends ConsumerWidget {
  const _VideosTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final projects = ref.watch(comicProjectsProvider);
    return projects.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (error, _) => ErrorView(message: errorMessage(error), onRetry: () => ref.invalidate(comicProjectsProvider)),
      data: (page) => RefreshIndicator(
        onRefresh: () => ref.read(comicProjectsProvider.notifier).refresh(),
        child: page.items.isEmpty
            ? ListView(
                padding: const EdgeInsets.all(24),
                children: const [
                  SizedBox(height: 60),
                  StudioEmptyState(
                    icon: Icons.movie_creation_outlined,
                    title: 'Chưa có video nào.',
                    subtitle: 'Các tập truyện tranh và video hoạt hình AI sẽ xuất hiện tại đây.',
                  ),
                ],
              )
            : NotificationListener<ScrollNotification>(
                onNotification: (notification) {
                  if (notification.metrics.extentAfter < 400) ref.read(comicProjectsProvider.notifier).loadMore();
                  return false;
                },
                child: ListView.separated(
                  padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
                  itemCount: page.items.length + (page.hasMore ? 1 : 0),
                  separatorBuilder: (_, _) => const SizedBox(height: 10),
                  itemBuilder: (context, index) {
                    if (index >= page.items.length) {
                      return const Padding(
                        padding: EdgeInsets.all(16),
                        child: Center(child: CircularProgressIndicator()),
                      );
                    }
                    return ComicJobTile(job: page.items[index]);
                  },
                ),
              ),
      ),
    );
  }
}

/// A comic project (episode or standalone video) in a list.
class ComicJobTile extends ConsumerWidget {
  const ComicJobTile({super.key, required this.job, this.showSeries = true});

  final Job job;
  final bool showSeries;

  Future<void> _delete(BuildContext context, WidgetRef ref, String title) async {
    if (!await confirmComic(context, 'Xóa "$title"? Video và toàn bộ file sẽ bị xóa vĩnh viễn.', action: 'Xóa')) {
      return;
    }
    if (!context.mounted) return;
    await runComicAction(context, () async {
      await ref.read(comicRepositoryProvider).deleteProject(job.id);
      ref.invalidate(comicProjectsProvider);
      ref.invalidate(comicSeriesListProvider);
    }, done: 'Đã xóa "$title"');
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final comic = ComicJobInfo.of(job);
    final title = showSeries ? (comic.title.isNotEmpty ? comic.title : job.title) : comic.episodeTitle();
    final series = comic.seriesId != null
        ? '${comic.seriesTitle.isEmpty ? 'Truyện' : comic.seriesTitle} · Tập ${comic.episodeNumber}'
        : 'Video lẻ';
    final readOnly = comicReadOnly(ref);

    return StudioCard(
      padding: EdgeInsets.zero,
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        onTap: () => context.push('/comic/projects/${job.id}'),
        title: Text(
          title.isEmpty ? job.id : title,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(fontWeight: FontWeight.w700),
        ),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                [if (showSeries) series, comic.meta()].join('\n'),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 12, color: AppColors.textSecondary, height: 1.4),
              ),
              if (job.error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text(
                    job.error!,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: AppColors.danger, fontSize: 12),
                  ),
                ),
              if (job.isActive) ...[
                const SizedBox(height: 8),
                ClipRRect(
                  borderRadius: BorderRadius.circular(999),
                  child: LinearProgressIndicator(value: comicProgress(job), minHeight: 5),
                ),
              ],
            ],
          ),
        ),
        isThreeLine: true,
        trailing: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            ComicStatusPill(job: job),
            if (!readOnly && !job.isActive)
              InkWell(
                onTap: () => _delete(context, ref, title),
                child: const Padding(
                  padding: EdgeInsets.only(top: 6),
                  child: Icon(Icons.delete_outline, size: 20, color: AppColors.textMuted),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class ComicStatusPill extends StatelessWidget {
  const ComicStatusPill({super.key, required this.job});

  final Job job;

  @override
  Widget build(BuildContext context) {
    final tone = job.isActive
        ? StudioStatusTone.warning
        : switch (job.status) {
            'completed' => StudioStatusTone.success,
            'failed' => StudioStatusTone.danger,
            _ => StudioStatusTone.neutral,
          };
    return ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 145),
      child: StudioStatusPill(
        label: comicStepLabel(job.step, jobStatusLabels[job.status] ?? job.status),
        tone: tone,
      ),
    );
  }
}
