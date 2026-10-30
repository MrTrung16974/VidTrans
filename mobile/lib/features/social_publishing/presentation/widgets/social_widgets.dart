import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../data/social_repository.dart';
import '../../domain/social_models.dart';
import '../providers/social_providers.dart';

class PlatformAvatar extends StatelessWidget {
  const PlatformAvatar(this.platform, {super.key, this.size = 40});

  final SocialPlatform platform;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(color: platform.color, borderRadius: BorderRadius.circular(size * 0.3)),
      alignment: Alignment.center,
      child: Icon(platform.icon, color: Colors.white, size: size * 0.55),
    );
  }
}

/// Connection card: status, connected accounts, connect / add / disconnect.
class PlatformConnectionCard extends ConsumerWidget {
  const PlatformConnectionCard({super.key, required this.platform});

  final SocialPlatform platform;

  Future<void> _connect(BuildContext context, WidgetRef ref) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final url = await ref.read(socialRepositoryProvider).connectUrl(platform);
      final opened = await launchUrl(url, mode: LaunchMode.externalApplication);
      if (!opened) messenger.showSnackBar(const SnackBar(content: Text('Không mở được trình duyệt')));
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    }
  }

  Future<void> _disconnect(BuildContext context, WidgetRef ref, SocialAccount account) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Ngắt kết nối ${platform.name}?'),
        content: Text('Tài khoản ${account.name} sẽ không đăng được video từ VidTrans nữa.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Hủy')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Ngắt kết nối')),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref.read(socialRepositoryProvider).disconnect(platform, account);
      ref.invalidate(socialAuthProvider(platform));
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(socialAuthProvider(platform));
    final readOnly = ref.watch(sessionProvider).valueOrNull?.isReadOnly ?? true;

    return StudioCard(
      margin: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              PlatformAvatar(platform),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(platform.name, style: Theme.of(context).textTheme.titleMedium),
                    const SizedBox(height: 2),
                    auth.when(
                      loading: () => const Text('Đang kiểm tra…', style: TextStyle(color: AppColors.muted)),
                      error: (error, _) => Text(errorMessage(error), style: const TextStyle(color: AppColors.redDark)),
                      data: (status) => Text(
                        _statusText(status),
                        style: TextStyle(color: status.connected ? AppColors.greenDark : AppColors.muted),
                      ),
                    ),
                  ],
                ),
              ),
              if (!readOnly && auth.hasValue && auth.value!.configured)
                TextButton.icon(
                  onPressed: () => _connect(context, ref),
                  icon: const Icon(Icons.add_link_rounded, size: 18),
                  label: Text(auth.value!.accounts.isEmpty ? 'Kết nối' : 'Thêm'),
                ),
            ],
          ),
          for (final account in auth.valueOrNull?.accounts ?? const <SocialAccount>[])
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Row(
                children: [
                  const Icon(Icons.account_circle_outlined, size: 20, color: AppColors.muted),
                  const SizedBox(width: 8),
                  Expanded(child: Text(account.name, overflow: TextOverflow.ellipsis)),
                  if (!readOnly)
                    IconButton(
                      tooltip: 'Ngắt kết nối',
                      visualDensity: VisualDensity.compact,
                      icon: const Icon(Icons.link_off_rounded, size: 20),
                      onPressed: () => _disconnect(context, ref, account),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  String _statusText(SocialAuthStatus status) {
    if (!status.configured) return 'Quản trị viên chưa cấu hình ${platform.name}';
    if (!status.connected) return 'Chưa kết nối tài khoản';
    if (platform.code == 'TIKTOK' && status.canDirectPublish == false) {
      return 'Cần kết nối lại để cấp quyền đăng video';
    }
    final count = status.accounts.length;
    return count > 1 ? 'Đã kết nối $count tài khoản' : 'Đã kết nối';
  }
}

StudioStatusTone attemptTone(PublishAttempt attempt) => switch (attempt.status) {
      'PUBLISHED' || 'DRAFT_DELIVERED' => StudioStatusTone.success,
      'FAILED' => StudioStatusTone.danger,
      'CANCELLED' => StudioStatusTone.neutral,
      'SCHEDULED_LOCAL' => StudioStatusTone.info,
      'NEEDS_RECONCILIATION' => StudioStatusTone.warning,
      _ => StudioStatusTone.brand,
    };
