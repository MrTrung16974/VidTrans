import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/account.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/config/env.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../../home/presentation/screens/home_shell.dart';
import '../../data/profile_repository.dart';
import '../providers/profile_providers.dart';
import '../widgets/quota_card.dart';
import 'billing_screen.dart';

const _roleLabels = {
  'owner': 'Chủ sở hữu',
  'admin': 'Quản trị viên',
  'editor': 'Biên tập viên',
  'viewer': 'Người xem',
};

class ProfileScreen extends ConsumerWidget {
  const ProfileScreen({super.key});

  Future<void> _editName(
    BuildContext context,
    WidgetRef ref,
    Account account,
  ) async {
    final name = await showDialog<String>(
      context: context,
      builder: (context) =>
          _DisplayNameDialog(initial: account.displayName ?? ''),
    );
    if (name == null || !context.mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref.read(profileRepositoryProvider).updateDisplayName(name);
      await ref.read(sessionProvider.notifier).reload();
      messenger.showSnackBar(
        const SnackBar(content: Text('Đã lưu tên hiển thị')),
      );
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    }
  }

  void _openBilling(BuildContext context) => Navigator.of(
    context,
  ).push(MaterialPageRoute<void>(builder: (_) => const BillingScreen()));

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final account = ref.watch(sessionProvider).valueOrNull;
    final authEnabled = ref.watch(authEnabledProvider);
    final billing = ref.watch(billingOverviewProvider);
    if (account == null) return const Scaffold(body: SizedBox.shrink());

    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: const StudioTopBar(),
      body: RefreshIndicator(
        color: AppColors.brand,
        onRefresh: () async {
          ref.invalidate(billingOverviewProvider);
          await ref.read(sessionProvider.notifier).reload();
        },
        child: ListView(
          padding: const EdgeInsets.fromLTRB(14, 14, 14, 28),
          children: [
            StudioPageHeader(
              eyebrow: 'Tài khoản & gắn logo',
              eyebrowIcon: Icons.manage_accounts_outlined,
              title: 'Quản lý tài khoản và thương hiệu',
              subtitle:
                  'Theo dõi quyền hạn, gói dịch vụ và tự động gắn logo thương hiệu của bạn vào video.',
              actions: [
                StudioStatusPill(
                  status: 'active',
                  label: (_roleLabels[account.role] ?? account.role).toUpperCase(),
                  compact: true,
                ),
              ],
            ),
            const SizedBox(height: 12),
            StudioCard(
              padding: const EdgeInsets.all(16),
              child: Row(
                children: [
                  Container(
                    width: 52,
                    height: 52,
                    decoration: BoxDecoration(
                      gradient: AppColors.brandGradient,
                      borderRadius: BorderRadius.circular(14),
                      boxShadow: AppColors.brandGlowShadow,
                    ),
                    alignment: Alignment.center,
                    child: Text(
                      account.name.characters.first.toUpperCase(),
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 21,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          account.name,
                          style: const TextStyle(
                            fontSize: 16.5,
                            fontWeight: FontWeight.w800,
                            color: AppColors.ink,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          account.email,
                          style: const TextStyle(
                            fontSize: 12.5,
                            color: AppColors.muted,
                          ),
                        ),
                      ],
                    ),
                  ),
                  if (!account.isReadOnly)
                    IconButton(
                      tooltip: 'Đổi tên hiển thị',
                      icon: const Icon(
                        Icons.edit_outlined,
                        color: AppColors.brand,
                      ),
                      onPressed: () => _editName(context, ref, account),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            billing.when(
              loading: () => const Padding(
                padding: EdgeInsets.all(24),
                child: Center(
                  child: CircularProgressIndicator(color: AppColors.brand),
                ),
              ),
              error: (error, _) => StudioCard(
                borderColor: AppColors.redBorder,
                child: ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Không tải được thông tin gói'),
                  subtitle: Text(errorMessage(error)),
                  trailing: IconButton(
                    icon: const Icon(Icons.refresh_rounded),
                    onPressed: () => ref.invalidate(billingOverviewProvider),
                  ),
                ),
              ),
              data: (overview) => overview.quota == null
                  ? const SizedBox.shrink()
                  : QuotaCard(
                      quota: overview.quota!,
                      plan: overview.account.plan ?? 'free',
                      paymentStatus: overview.paymentStatus,
                      onTap: () => _openBilling(context),
                    ),
            ),
            const SizedBox(height: 12),
            StudioCard(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Column(
                children: [
                  ListTile(
                    leading: const Icon(
                      Icons.workspace_premium_outlined,
                      color: AppColors.brand,
                    ),
                    title: const Text('Gói dịch vụ & thanh toán'),
                    trailing: const Icon(Icons.chevron_right_rounded),
                    onTap: () => _openBilling(context),
                  ),
                  const Divider(height: 1),
                  ListTile(
                    leading: const Icon(
                      Icons.tune_rounded,
                      color: AppColors.teal,
                    ),
                    title: const Text('Logo & cấu hình chèn logo'),
                    subtitle: const Text('Chọn "Chèn logo" khi tạo video dịch hoặc video truyện'),
                    trailing: const Icon(Icons.chevron_right_rounded),
                    onTap: () => context.push('/branding'),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            StudioCard(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Column(
                children: [
                  ListTile(
                    title: const Text('Vai trò'),
                    trailing: StudioEyebrow(
                      label: _roleLabels[account.role] ?? account.role,
                    ),
                  ),
                  if (account.videoRetentionDays != null) ...[
                    const Divider(height: 1),
                    ListTile(
                      title: const Text('Lưu video'),
                      trailing: Text(
                        '${account.videoRetentionDays} ngày',
                        style: const TextStyle(fontWeight: FontWeight.w700),
                      ),
                    ),
                  ],
                  const Divider(height: 1),
                  ListTile(
                    title: const Text('Máy chủ'),
                    subtitle: Text(Env.apiBase),
                  ),
                  const Divider(height: 1),
                  ListTile(
                    title: const Text('Xác thực'),
                    trailing: StudioEyebrow(
                      label: authEnabled == false ? 'ĐANG TẮT' : 'ĐANG BẬT',
                    ),
                  ),
                ],
              ),
            ),
            if (authEnabled != false) ...[
              const SizedBox(height: 16),
              OutlinedButton.icon(
                onPressed: () => ref.read(sessionProvider.notifier).logout(),
                icon: const Icon(Icons.logout_rounded, color: AppColors.red),
                label: const Text(
                  'Đăng xuất',
                  style: TextStyle(color: AppColors.red),
                ),
                style: OutlinedButton.styleFrom(
                  side: const BorderSide(color: AppColors.redBorder),
                  backgroundColor: AppColors.redSoft,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _DisplayNameDialog extends StatefulWidget {
  const _DisplayNameDialog({required this.initial});

  final String initial;

  @override
  State<_DisplayNameDialog> createState() => _DisplayNameDialogState();
}

class _DisplayNameDialogState extends State<_DisplayNameDialog> {
  late final _controller = TextEditingController(text: widget.initial);
  final _formKey = GlobalKey<FormState>();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _submit() {
    if (_formKey.currentState!.validate()) {
      Navigator.pop(context, _controller.text.trim());
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Tên hiển thị'),
      content: Form(
        key: _formKey,
        child: TextFormField(
          controller: _controller,
          autofocus: true,
          maxLength: 120,
          onFieldSubmitted: (_) => _submit(),
          validator: (value) => (value == null || value.trim().isEmpty)
              ? 'Nhập tên hiển thị'
              : null,
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Hủy'),
        ),
        FilledButton(onPressed: _submit, child: const Text('Lưu')),
      ],
    );
  }
}
