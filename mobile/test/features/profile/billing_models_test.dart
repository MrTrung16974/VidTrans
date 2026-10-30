import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/features/profile/domain/billing_models.dart';

void main() {
  final json = {
    'account': {'user_id': 'u1', 'email': 'a@b.c', 'role': 'editor', 'plan': 'creator', 'payment_status': 'active'},
    'quota': {'period': '2026-10', 'limit_minutes': 300, 'reserved_minutes': 20, 'consumed_minutes': 100, 'remaining_minutes': 180},
    'plans': [
      {'key': 'free', 'name': 'Free', 'amount': 0, 'minutes': 30},
      {'key': 'enterprise', 'name': 'Enterprise', 'amount': null, 'minutes': null},
    ],
    'providers': {'bank': true, 'momo': false},
    'provider_notes': {'bank': 'VietQR'},
    'orders': [
      {
        'order_id': 'o1', 'provider': 'bank', 'plan': 'pro', 'amount': 499000, 'status': 'pending',
        'transfer_content': 'VIDTRANS ABC', 'created_at': '2026-10-01T00:00:00+00:00', 'customer_reported_at': '2026-10-01T01:00:00+00:00',
      },
    ],
  };

  test('overview parses account, quota from the server and orders', () {
    final overview = BillingOverview.fromJson(json);
    expect(overview.account.plan, 'creator');
    expect(overview.paymentStatus, 'active');
    expect(overview.quota!.usedMinutes, 120);
    expect(overview.quota!.remainingMinutes, 180); // shown as reported, not recomputed
    expect(overview.quota!.usedFraction, closeTo(0.4, 1e-9));
    expect(overview.providers, {'bank': true, 'momo': false});
    expect(overview.plans.last.isPaid, isFalse);
    final order = overview.orders.single;
    expect(order.statusLabel, 'Chờ đối soát');
    expect(order.canReportTransfer, isFalse);
  });

  test('unlimited quota and money formatting', () {
    final quota = Quota.fromJson({'period': '2026-10', 'limit_minutes': null, 'consumed_minutes': 5});
    expect(quota.unlimited, isTrue);
    expect(quota.usedFraction, 1);
    expect(formatMoney(null), 'Liên hệ');
    expect(formatMoney(199000), contains('199.000'));
  });
}
