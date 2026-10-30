import 'package:flutter/material.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../domain/billing_models.dart';

/// Monthly minutes, worded like the web billing page.
class QuotaCard extends StatelessWidget {
  const QuotaCard({
    super.key,
    required this.quota,
    required this.plan,
    this.paymentStatus,
    this.onTap,
  });

  final Quota quota;
  final String plan;
  final String? paymentStatus;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final fraction = quota.usedFraction;
    final percent = (fraction * 100).round();
    final color = quota.unlimited
        ? AppColors.brand
        : percent > 90
            ? AppColors.red
            : percent > 70
                ? AppColors.amber
                : AppColors.brand;
    final usage = quota.unlimited
        ? '${quota.usedMinutes} phút (Không giới hạn)'
        : '${quota.usedMinutes} / ${quota.limitMinutes} phút ($percent%)';
    final remaining = quota.unlimited
        ? 'Gói Doanh nghiệp không giới hạn thời lượng'
        : 'Còn lại: ${quota.remainingMinutes ?? 0} phút'
            '${quota.reservedMinutes > 0 ? ' (Đang giữ chỗ: ${quota.reservedMinutes} phút)' : ''}';

    return StudioCard(
      topAccentColor: AppColors.brand,
      borderColor: AppColors.brandBorder,
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 38,
                height: 38,
                decoration: BoxDecoration(
                  gradient: AppColors.brandGradient,
                  borderRadius: BorderRadius.circular(10),
                ),
                alignment: Alignment.center,
                child: const Icon(
                  Icons.workspace_premium_rounded,
                  color: Colors.white,
                  size: 20,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Gói ${plan.toUpperCase()}',
                      style: const TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w800,
                        color: AppColors.ink,
                      ),
                    ),
                    Text(
                      'Thời lượng kỳ ${quota.period}',
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.muted,
                      ),
                    ),
                  ],
                ),
              ),
              if (paymentStatus != null)
                StudioStatusPill(
                  status: paymentStatus!,
                  label: paymentStatusLabels[paymentStatus] ?? paymentStatus!,
                  compact: true,
                ),
            ],
          ),
          const SizedBox(height: 14),
          Text(
            usage,
            style: const TextStyle(
              fontSize: 13.5,
              fontWeight: FontWeight.w700,
              color: AppColors.ink,
            ),
          ),
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: LinearProgressIndicator(
              value: fraction,
              color: color,
              backgroundColor: AppColors.line,
              minHeight: 7,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            remaining,
            style: const TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w600,
              color: AppColors.muted,
            ),
          ),
        ],
      ),
    );
  }
}
