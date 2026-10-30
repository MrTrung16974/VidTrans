import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../domain/branding_models.dart';
import '../providers/branding_providers.dart';
import '../widgets/branding_widgets.dart';

/// Logo tab: history of "stamp my logo on this video" jobs.
class BrandingScreen extends ConsumerWidget {
  const BrandingScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final history = ref.watch(brandingHistoryProvider);
    final readOnly = ref.watch(sessionProvider).valueOrNull?.isReadOnly ?? true;

    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: const VidTransBrandTitle(subtitle: 'LOGO BRANDING STUDIO'),
        actions: [
          IconButton(
            tooltip: 'Cấu hình logo',
            icon: const Icon(Icons.tune_rounded),
            onPressed: () => context.push('/branding/settings'),
          ),
          const SizedBox(width: 4),
        ],
      ),
      floatingActionButton: readOnly
          ? null
          : FloatingActionButton.extended(
              onPressed: () => context.push('/branding/new'),
              icon: const Icon(Icons.upload_file_rounded, size: 19),
              label: const Text('Gửi video'),
            ),
      body: history.when(
        loading: () => const Center(
          child: CircularProgressIndicator(color: AppColors.brand),
        ),
        error: (error, _) => ErrorView(
          message: errorMessage(error),
          onRetry: () => ref.invalidate(brandingHistoryProvider),
        ),
        data: (history) => RefreshIndicator(
          color: AppColors.brand,
          onRefresh: () => ref.read(brandingHistoryProvider.notifier).refresh(),
          child: history.items.isEmpty
              ? ListView(
                  children: [
                    const SizedBox(height: 40),
                    StudioEmptyState(
                      icon: Icons.branding_watermark_outlined,
                      title: 'Chưa có video gắn logo nào',
                      subtitle:
                          'Cấu hình logo thương hiệu và gửi video để tự động nhận diện, che logo cũ và đóng dấu logo mới.',
                      action: readOnly
                          ? null
                          : StudioGradientButton(
                              expanded: false,
                              icon: Icons.upload_file_rounded,
                              label: 'Gửi video gắn logo',
                              onPressed: () => context.push('/branding/new'),
                            ),
                    ),
                  ],
                )
              : ListView.separated(
                  padding: const EdgeInsets.fromLTRB(16, 14, 16, 96),
                  itemCount: history.items.length,
                  separatorBuilder: (_, _) => const SizedBox(height: 10),
                  itemBuilder: (context, index) =>
                      _BrandingJobTile(job: history.items[index]),
                ),
        ),
      ),
    );
  }
}

class _BrandingJobTile extends StatelessWidget {
  const _BrandingJobTile({required this.job});

  final BrandingJob job;

  @override
  Widget build(BuildContext context) {
    final created = job.createdAt;
    final detail = job.placementText ?? job.stepDetail;
    final meta = [
      if (created != null) DateFormat('dd/MM HH:mm').format(created),
      if (job.needsReview) 'Cần xác nhận vị trí logo',
      ?detail,
    ].join(' · ');

    return StudioCard(
      padding: const EdgeInsets.all(14),
      borderColor: job.needsReview ? AppColors.amberBorder : AppColors.line,
      onTap: () => context.push('/branding/${job.id}'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
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
                  Icons.branding_watermark_rounded,
                  color: AppColors.brand,
                  size: 20,
                ),
              ),
              const SizedBox(width: 11),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      job.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 14.5,
                        fontWeight: FontWeight.w700,
                        color: AppColors.ink,
                      ),
                    ),
                    if (meta.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text(
                        meta,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 12,
                          color: AppColors.muted,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(width: 8),
              BrandingStatusChip(job.status),
            ],
          ),
          if (job.isActive) ...[
            const SizedBox(height: 10),
            ClipRRect(
              borderRadius: BorderRadius.circular(999),
              child: LinearProgressIndicator(
                value: job.progress,
                minHeight: 6,
              ),
            ),
          ],
        ],
      ),
    );
  }
}
