import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../data/profile_repository.dart';
import '../../domain/billing_models.dart';
import '../providers/profile_providers.dart';
import '../widgets/quota_card.dart';

/// Plans, payment orders and quota (same flows as the web #billing view).
class BillingScreen extends ConsumerWidget {
  const BillingScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final overview = ref.watch(billingOverviewProvider);
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('Gói dịch vụ & thanh toán')),
      body: overview.when(
        skipLoadingOnRefresh: true,
        loading: () => const Center(
          child: CircularProgressIndicator(color: AppColors.brand),
        ),
        error: (error, _) => ErrorView(
          message: errorMessage(error),
          onRetry: () => ref.invalidate(billingOverviewProvider),
        ),
        data: (overview) => RefreshIndicator(
          color: AppColors.brand,
          onRefresh: () => ref.refresh(billingOverviewProvider.future),
          child: _BillingBody(overview: overview),
        ),
      ),
    );
  }
}

class _BillingBody extends ConsumerWidget {
  const _BillingBody({required this.overview});

  final BillingOverview overview;

  ProfileRepository _repo(WidgetRef ref) => ref.read(profileRepositoryProvider);

  Future<void> _run(
    BuildContext context,
    WidgetRef ref,
    Future<void> Function() action,
    String done,
  ) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await action();
      messenger.showSnackBar(SnackBar(content: Text(done)));
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    } finally {
      ref.invalidate(billingOverviewProvider);
    }
  }

  Future<bool> _confirm(BuildContext context, String message) async =>
      await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          content: Text(message),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Không'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Đồng ý'),
            ),
          ],
        ),
      ) ==
      true;

  Future<void> _choosePlan(
    BuildContext context,
    WidgetRef ref,
    Plan plan,
  ) async {
    final provider = await showModalBottomSheet<String>(
      context: context,
      builder: (context) => _ProviderSheet(plan: plan, overview: overview),
    );
    if (provider == null || !context.mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      final order =
          await _repo(ref).createOrder(plan: plan.key, provider: provider);
      ref.invalidate(billingOverviewProvider);
      if (!context.mounted) return;
      messenger.showSnackBar(
        const SnackBar(content: Text('Đã tạo yêu cầu thanh toán thành công')),
      );
      await _showOrder(context, ref, order);
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    }
  }

  Future<void> _showOrder(
    BuildContext context,
    WidgetRef ref,
    PaymentOrder order,
  ) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (sheetContext) => _PaymentSheet(
        order: order,
        onReport: () async {
          Navigator.pop(sheetContext);
          await _run(
            context,
            ref,
            () => _repo(ref).reportTransfer(order.id),
            'Đã ghi nhận. Gói sẽ được kích hoạt sau khi quản trị viên đối soát giao dịch.',
          );
        },
        onCancel: () async {
          Navigator.pop(sheetContext);
          if (await _confirm(
                context,
                'Bạn muốn hủy yêu cầu thanh toán này?',
              ) &&
              context.mounted) {
            await _run(
              context,
              ref,
              () => _repo(ref).cancelOrder(order.id),
              'Đã hủy yêu cầu thanh toán',
            );
          }
        },
      ),
    );
  }

  Future<void> _viewOrder(
    BuildContext context,
    WidgetRef ref,
    PaymentOrder order,
  ) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final detail = await _repo(ref).order(order.id);
      if (!context.mounted) return;
      if (detail.bank == null && detail.checkoutUrl == null) {
        messenger.showSnackBar(
          const SnackBar(
            content: Text('Đơn này không sử dụng chuyển khoản VietQR'),
          ),
        );
        return;
      }
      await _showOrder(context, ref, detail);
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final readOnly = ref.watch(sessionProvider).valueOrNull?.isReadOnly ?? true;
    final currentPlan = overview.account.plan ?? 'free';

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 32),
      children: [
        if (overview.quota != null)
          QuotaCard(
            quota: overview.quota!,
            plan: currentPlan,
            paymentStatus: overview.paymentStatus,
          ),
        const SizedBox(height: 18),
        const Text(
          'CÁC GÓI DỊCH VỤ STUDIO',
          style: TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.w800,
            letterSpacing: 0.5,
            color: AppColors.inkSoft,
          ),
        ),
        const SizedBox(height: 10),
        for (final plan in overview.plans)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: StudioCard(
              topAccentColor:
                  plan.key == currentPlan ? AppColors.brand : AppColors.teal,
              borderColor:
                  plan.key == currentPlan ? AppColors.brand : AppColors.line,
              padding: const EdgeInsets.all(14),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          plan.name,
                          style: const TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.w800,
                            color: AppColors.ink,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          '${formatMoney(plan.amount)}${plan.isPaid ? '/tháng' : ''} · '
                          '${plan.minutes == null ? 'Không giới hạn' : '${plan.minutes} phút/tháng'}',
                          style: const TextStyle(
                            fontSize: 12.5,
                            fontWeight: FontWeight.w600,
                            color: AppColors.muted,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 10),
                  if (plan.key == currentPlan)
                    const StudioStatusPill(
                      status: 'active',
                      label: 'Gói hiện tại',
                      compact: true,
                    )
                  else if (!readOnly && plan.isPaid)
                    FilledButton.tonal(
                      // Inside a Row: override the theme's full-width minimum size.
                      style: FilledButton.styleFrom(minimumSize: const Size(0, 40)),
                      onPressed: () => _choosePlan(context, ref, plan),
                      child: const Text('Chọn gói'),
                    )
                  else if (!readOnly && plan.key == 'free')
                    OutlinedButton(
                      style: OutlinedButton.styleFrom(minimumSize: const Size(0, 40)),
                      onPressed: () async {
                        if (!await _confirm(
                              context,
                              'Bạn muốn chuyển về gói Free (30 phút/tháng)?',
                            ) ||
                            !context.mounted) {
                          return;
                        }
                        await _run(context, ref, () async {
                          await _repo(ref).switchToFree();
                          await ref.read(sessionProvider.notifier).reload();
                        }, 'Đã chuyển về gói Free thành công');
                      },
                      child: const Text('Chuyển về Free'),
                    )
                  else if (!readOnly)
                    const Text(
                      'Liên hệ',
                      style: TextStyle(
                        fontWeight: FontWeight.w700,
                        color: AppColors.muted,
                      ),
                    ),
                ],
              ),
            ),
          ),
        const SizedBox(height: 18),
        const Text(
          'LỊCH SỬ GIAO DỊCH',
          style: TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.w800,
            letterSpacing: 0.5,
            color: AppColors.inkSoft,
          ),
        ),
        const SizedBox(height: 10),
        if (overview.orders.isEmpty)
          const StudioCard(
            child: Center(
              child: Text(
                'Chưa có giao dịch',
                style: TextStyle(color: AppColors.muted),
              ),
            ),
          ),
        for (final order in overview.orders)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: StudioCard(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          order.transferContent,
                          style: const TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w800,
                            color: AppColors.ink,
                          ),
                        ),
                      ),
                      Text(
                        formatMoney(order.amount),
                        style: const TextStyle(
                          fontSize: 14,
                          fontWeight: FontWeight.w800,
                          color: AppColors.brandDark,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  Text(
                    [
                      if (order.createdAt != null)
                        DateFormat('dd/MM/yyyy HH:mm').format(order.createdAt!),
                      'Gói ${order.plan.toUpperCase()}',
                      order.statusLabel,
                    ].join(' · '),
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.muted,
                    ),
                  ),
                  if (order.isPending && !readOnly) ...[
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 8,
                      children: [
                        TextButton.icon(
                          onPressed: () => _viewOrder(context, ref, order),
                          icon: const Icon(Icons.qr_code_rounded, size: 16),
                          label: const Text('Xem QR'),
                        ),
                        if (order.canReportTransfer)
                          TextButton(
                            onPressed: () => _run(
                              context,
                              ref,
                              () => _repo(ref).reportTransfer(order.id),
                              'Đã ghi nhận. Gói sẽ được kích hoạt sau khi quản trị viên đối soát giao dịch.',
                            ),
                            child: const Text('Đã chuyển khoản'),
                          ),
                        TextButton(
                          onPressed: () async {
                            if (await _confirm(
                                  context,
                                  'Bạn muốn hủy yêu cầu thanh toán này?',
                                ) &&
                                context.mounted) {
                              await _run(
                                context,
                                ref,
                                () => _repo(ref).cancelOrder(order.id),
                                'Đã hủy yêu cầu thanh toán',
                              );
                            }
                          },
                          child: const Text(
                            'Hủy',
                            style: TextStyle(color: AppColors.red),
                          ),
                        ),
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ),
      ],
    );
  }
}

class _ProviderSheet extends StatefulWidget {
  const _ProviderSheet({required this.plan, required this.overview});

  final Plan plan;
  final BillingOverview overview;

  @override
  State<_ProviderSheet> createState() => _ProviderSheetState();
}

class _ProviderSheetState extends State<_ProviderSheet> {
  late String? _provider = widget.overview.providers['bank'] == true
      ? 'bank'
      : widget.overview.providers.entries
          .where((e) => e.value)
          .map((e) => e.key)
          .firstOrNull;

  @override
  Widget build(BuildContext context) {
    final overview = widget.overview;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            StudioStepHeader(
              icon: Icons.workspace_premium_rounded,
              title:
                  'Gói ${widget.plan.name} · ${formatMoney(widget.plan.amount)}',
              subtitle: 'Chọn phương thức thanh toán để kích hoạt gói',
            ),
            const SizedBox(height: 12),
            RadioGroup<String>(
              groupValue: _provider,
              onChanged: (value) => setState(() => _provider = value),
              child: Column(
                children: [
                  for (final entry in overview.providers.entries)
                    RadioListTile<String>(
                      value: entry.key,
                      enabled: entry.value,
                      title: Text(
                        paymentProviderLabels[entry.key] ?? entry.key,
                        style: const TextStyle(fontWeight: FontWeight.w700),
                      ),
                      subtitle: Text(
                        entry.value
                            ? overview.providerNotes[entry.key] ?? ''
                            : 'Chưa khả dụng',
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            StudioGradientButton(
              onPressed: _provider == null
                  ? null
                  : () => Navigator.pop(context, _provider),
              icon: Icons.qr_code_2_rounded,
              label: 'Tạo yêu cầu thanh toán',
            ),
          ],
        ),
      ),
    );
  }
}

class _PaymentSheet extends StatelessWidget {
  const _PaymentSheet({
    required this.order,
    required this.onReport,
    required this.onCancel,
  });

  final PaymentOrder order;
  final VoidCallback onReport;
  final VoidCallback onCancel;

  void _copy(BuildContext context, String value) {
    Clipboard.setData(ClipboardData(text: value));
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Đã sao chép: $value')),
    );
  }

  @override
  Widget build(BuildContext context) {
    final bank = order.bank;
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            StudioStepHeader(
              icon: Icons.qr_code_scanner_rounded,
              title: 'Yêu cầu thanh toán ${order.plan.toUpperCase()}',
              subtitle: formatMoney(order.amount),
            ),
            const SizedBox(height: 14),
            if (bank != null) ...[
              if (bank.qrUrl.isNotEmpty)
                Center(
                  child: Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(16),
                      border: Border.all(color: AppColors.lineStrong),
                    ),
                    child: Image.network(
                      bank.qrUrl,
                      width: 230,
                      height: 230,
                      fit: BoxFit.contain,
                    ),
                  ),
                ),
              const SizedBox(height: 8),
              const Center(
                child: Text(
                  'Quét mã bằng App ngân hàng bất kỳ (VietQR 24/7)',
                  style: TextStyle(fontSize: 12.5, color: AppColors.muted),
                ),
              ),
              const SizedBox(height: 12),
              Container(
                decoration: BoxDecoration(
                  color: AppColors.surfaceSubtle,
                  borderRadius: BorderRadius.circular(14),
                  border: Border.all(color: AppColors.line),
                ),
                child: Column(
                  children: [
                    ListTile(
                      dense: true,
                      title: const Text('Ngân hàng thụ hưởng'),
                      subtitle: Text(bank.name),
                    ),
                    ListTile(
                      dense: true,
                      title: const Text('Số tài khoản'),
                      subtitle: Text(bank.accountNo),
                      trailing: IconButton(
                        icon: const Icon(Icons.copy_rounded, size: 18),
                        onPressed: () => _copy(context, bank.accountNo),
                      ),
                    ),
                    ListTile(
                      dense: true,
                      title: const Text('Tên chủ tài khoản'),
                      subtitle: Text(bank.accountName),
                    ),
                    ListTile(
                      dense: true,
                      title: const Text('Nội dung chuyển khoản (bắt buộc)'),
                      subtitle: Text(
                        order.transferContent,
                        style: const TextStyle(
                          fontWeight: FontWeight.w800,
                          color: AppColors.brandDark,
                        ),
                      ),
                      trailing: IconButton(
                        icon: const Icon(Icons.copy_rounded, size: 18),
                        onPressed: () => _copy(context, order.transferContent),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 10),
              const Text(
                'Mã QR đã chứa đúng số tiền và cú pháp. Sau khi chuyển khoản, hãy bấm báo đã chuyển khoản; '
                'gói được kích hoạt khi quản trị viên đối soát xong giao dịch.',
                style: TextStyle(fontSize: 12, color: AppColors.muted),
              ),
              const SizedBox(height: 12),
              StudioGradientButton(
                onPressed: order.customerReportedAt == null ? onReport : null,
                icon: Icons.check_circle_outline_rounded,
                label: order.customerReportedAt == null
                    ? 'Tôi đã chuyển khoản'
                    : 'Đã báo chuyển khoản — chờ đối soát',
              ),
            ] else if (order.checkoutUrl != null) ...[
              StudioGradientButton(
                onPressed: () => launchUrl(
                  Uri.parse(order.checkoutUrl!),
                  mode: LaunchMode.externalApplication,
                ),
                icon: Icons.open_in_new_rounded,
                label: 'Mở trang thanh toán',
              ),
              TextButton.icon(
                onPressed: () => _copy(context, order.checkoutUrl!),
                icon: const Icon(Icons.copy_rounded),
                label: const Text('Sao chép liên kết thanh toán'),
              ),
            ],
            const SizedBox(height: 8),
            OutlinedButton(
              onPressed: onCancel,
              child: const Text('Hủy yêu cầu này'),
            ),
          ],
        ),
      ),
    );
  }
}
