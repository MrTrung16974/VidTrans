import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../../home/presentation/screens/home_shell.dart';
import '../../domain/social_models.dart';
import '../providers/social_providers.dart';
import '../widgets/social_widgets.dart';

/// Social publishing home: connected accounts, then pick a finished video.
class SocialHubScreen extends ConsumerStatefulWidget {
  const SocialHubScreen({super.key});

  @override
  ConsumerState<SocialHubScreen> createState() => _SocialHubScreenState();
}

class _SocialHubScreenState extends ConsumerState<SocialHubScreen> {
  final _searchController = TextEditingController();
  String _query = '';

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final jobs = ref.watch(publishableJobsProvider);

    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: const StudioTopBar(),
      body: RefreshIndicator(
        onRefresh: () async {
          for (final platform in SocialPlatform.all) {
            ref.invalidate(socialAuthProvider(platform));
          }
          ref.invalidate(publishableJobsProvider);
          await ref.read(publishableJobsProvider.future).catchError((_) => const <Never>[]);
        },
        child: ListView(
          padding: EdgeInsets.fromLTRB(
            14,
            14,
            14,
            studioDockBottomPadding(context),
          ),
          children: [
            const StudioPageHeader(
              eyebrow: 'Đăng bài mạng xã hội',
              eyebrowIcon: Icons.share_rounded,
              eyebrowNote: 'TikTok · YouTube · Facebook',
              title: 'Đăng bài mạng xã hội',
              subtitle:
                  'Kết nối tài khoản mạng xã hội, chọn video đã hoàn tất để đăng ngay, lưu nháp hoặc hẹn giờ xuất bản.',
            ),
            const SizedBox(height: 14),
            const StudioStepHeader(
              stepNumber: 1,
              icon: Icons.link_rounded,
              title: 'Kết nối tài khoản',
              subtitle: 'Đăng nhập nền tảng một lần, VidTrans sẽ đăng video thay bạn',
            ),
            const SizedBox(height: 12),
            for (final platform in SocialPlatform.all) PlatformConnectionCard(platform: platform),
            const SizedBox(height: 12),
            const StudioStepHeader(
              stepNumber: 2,
              icon: Icons.video_library_rounded,
              title: 'Chọn video để đăng',
              subtitle: 'Chỉ video đã xử lý xong mới đăng được',
            ),
            const SizedBox(height: 10),
            TextField(
              controller: _searchController,
              onChanged: (value) => setState(() => _query = value.trim().toLowerCase()),
              decoration: InputDecoration(
                isDense: true,
                hintText: 'Tìm video theo tên file, loại công việc hoặc mã job…',
                prefixIcon: const Icon(Icons.search_rounded, size: 19),
                suffixIcon: _query.isNotEmpty
                    ? IconButton(
                        icon: const Icon(Icons.clear_rounded, size: 18),
                        onPressed: () {
                          _searchController.clear();
                          setState(() => _query = '');
                        },
                      )
                    : null,
              ),
            ),
            const SizedBox(height: 12),
            jobs.when(
              loading: () => const Padding(
                padding: EdgeInsets.all(24),
                child: Center(child: CircularProgressIndicator()),
              ),
              error: (error, _) => ErrorView(
                message: errorMessage(error),
                onRetry: () => ref.invalidate(publishableJobsProvider),
              ),
              data: (items) {
                final filtered = _query.isEmpty
                    ? items
                    : items
                        .where(
                          (job) =>
                              job.title.toLowerCase().contains(_query) ||
                              job.actionLabel.toLowerCase().contains(_query) ||
                              job.id.toLowerCase().contains(_query),
                        )
                        .toList();
                if (items.isEmpty) {
                  return const StudioEmptyState(
                    icon: Icons.movie_filter_outlined,
                    title: 'Chưa có video hoàn thành',
                    subtitle: 'Video dịch, truyện tranh hay logo xử lý xong sẽ hiện ở đây.',
                  );
                }
                if (filtered.isEmpty) {
                  return const StudioEmptyState(
                    icon: Icons.search_off_rounded,
                    title: 'Không tìm thấy video phù hợp',
                    subtitle: 'Hãy thử từ khóa khác hoặc xóa bộ lọc tìm kiếm.',
                  );
                }
                return Column(
                  children: [
                    for (final job in filtered)
                      StudioCard(
                        margin: const EdgeInsets.only(bottom: 10),
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                        onTap: () => context.push('/social/publish/${job.id}'),
                        child: Row(
                          children: [
                            Container(
                              width: 40,
                              height: 40,
                              decoration: BoxDecoration(
                                color: AppColors.brandSoft,
                                borderRadius: BorderRadius.circular(11),
                                border: Border.all(color: AppColors.brandBorder),
                              ),
                              alignment: Alignment.center,
                              child: const Icon(
                                Icons.play_circle_outline_rounded,
                                color: AppColors.brand,
                              ),
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    job.title,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: Theme.of(context).textTheme.titleSmall,
                                  ),
                                  const SizedBox(height: 5),
                                  Wrap(
                                    spacing: 6,
                                    runSpacing: 4,
                                    crossAxisAlignment: WrapCrossAlignment.center,
                                    children: [
                                      Container(
                                        padding: const EdgeInsets.symmetric(
                                          horizontal: 7,
                                          vertical: 2,
                                        ),
                                        decoration: BoxDecoration(
                                          color: AppColors.surfaceSubtle,
                                          borderRadius: BorderRadius.circular(6),
                                          border: Border.all(color: AppColors.line),
                                        ),
                                        child: Text(
                                          job.actionLabel,
                                          style: const TextStyle(
                                            fontSize: 10.5,
                                            fontWeight: FontWeight.w700,
                                            color: AppColors.inkSoft,
                                          ),
                                        ),
                                      ),
                                      const StudioStatusPill(
                                        status: 'completed',
                                        label: 'Sẵn sàng đăng',
                                        compact: true,
                                      ),
                                      if (job.createdAt != null)
                                        Text(
                                          DateFormat('dd/MM/yyyy HH:mm').format(job.createdAt!),
                                          style: Theme.of(context).textTheme.bodySmall,
                                        ),
                                    ],
                                  ),
                                ],
                              ),
                            ),
                            const Icon(Icons.chevron_right_rounded, color: AppColors.muted),
                          ],
                        ),
                      ),
                  ],
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}
