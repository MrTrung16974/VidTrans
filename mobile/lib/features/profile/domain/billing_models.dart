import 'package:intl/intl.dart';

import '../../../core/auth/account.dart';

const paymentStatusLabels = {
  'trial': 'Đang dùng thử',
  'active': 'Đang hoạt động',
  'past_due': 'Cần thanh toán',
  'cancelled': 'Đã hủy',
  'exempt': 'Miễn thanh toán',
};

const orderStatusLabels = {
  'pending': 'Chờ thanh toán',
  'paid': 'Đã thanh toán',
  'failed': 'Thất bại',
  'expired': 'Hết hạn',
  'cancelled': 'Đã hủy',
};

const paymentProviderLabels = {
  'bank': 'Chuyển khoản Ngân hàng (VietQR)',
  'momo': 'Ví điện tử MoMo',
  'zalopay': 'Ví ZaloPay',
};

/// "199.000 ₫", or "Liên hệ" for plans without a price (Enterprise).
String formatMoney(num? amount) => amount == null
    ? 'Liên hệ'
    : NumberFormat.currency(locale: 'vi_VN', symbol: '₫', decimalDigits: 0).format(amount);

/// Monthly minutes as reported by the backend (TenantGovernance.overview).
/// The app only displays these numbers; it never computes quota itself.
class Quota {
  const Quota({
    required this.period,
    required this.limitMinutes,
    required this.reservedMinutes,
    required this.consumedMinutes,
    required this.remainingMinutes,
  });

  final String period;

  /// Null means unlimited (Enterprise).
  final int? limitMinutes;
  final int reservedMinutes;
  final int consumedMinutes;
  final int? remainingMinutes;

  bool get unlimited => limitMinutes == null;
  int get usedMinutes => reservedMinutes + consumedMinutes;

  /// Share of the limit used, for the progress bar (0..1).
  double get usedFraction {
    final limit = limitMinutes;
    if (limit == null || limit <= 0) return 1;
    return (usedMinutes / limit).clamp(0, 1).toDouble();
  }

  factory Quota.fromJson(Map<String, dynamic> json) => Quota(
        period: json['period']?.toString() ?? '',
        limitMinutes: (json['limit_minutes'] as num?)?.toInt(),
        reservedMinutes: (json['reserved_minutes'] as num?)?.toInt() ?? 0,
        consumedMinutes: (json['consumed_minutes'] as num?)?.toInt() ?? 0,
        remainingMinutes: (json['remaining_minutes'] as num?)?.toInt(),
      );
}

class Plan {
  const Plan({required this.key, required this.name, required this.amount, required this.minutes});

  final String key;
  final String name;
  final int? amount;
  final int? minutes;

  bool get isPaid => (amount ?? 0) > 0;

  factory Plan.fromJson(Map<String, dynamic> json) => Plan(
        key: json['key'].toString(),
        name: json['name']?.toString() ?? json['key'].toString(),
        amount: (json['amount'] as num?)?.toInt(),
        minutes: (json['minutes'] as num?)?.toInt(),
      );
}

class BankTransfer {
  const BankTransfer({required this.name, required this.accountNo, required this.accountName, required this.qrUrl});

  final String name;
  final String accountNo;
  final String accountName;

  /// Public VietQR image carrying the amount and transfer content.
  final String qrUrl;

  static BankTransfer? fromJson(Object? json) {
    if (json is! Map) return null;
    return BankTransfer(
      name: json['name']?.toString() ?? '',
      accountNo: json['account_no']?.toString() ?? '',
      accountName: json['account_name']?.toString() ?? '',
      qrUrl: json['qr_url']?.toString() ?? '',
    );
  }
}

class PaymentOrder {
  const PaymentOrder({
    required this.id,
    required this.provider,
    required this.plan,
    required this.amount,
    required this.status,
    required this.transferContent,
    this.createdAt,
    this.customerReportedAt,
    this.bank,
    this.checkoutUrl,
  });

  final String id;
  final String provider;
  final String plan;
  final int? amount;
  final String status;
  final String transferContent;
  final DateTime? createdAt;
  final String? customerReportedAt;
  final BankTransfer? bank;

  /// MoMo / ZaloPay payment page.
  final String? checkoutUrl;

  bool get isPending => status == 'pending';
  bool get canReportTransfer => isPending && provider == 'bank' && customerReportedAt == null;

  String get statusLabel =>
      isPending && customerReportedAt != null ? 'Chờ đối soát' : orderStatusLabels[status] ?? status;

  factory PaymentOrder.fromJson(Map<String, dynamic> json) => PaymentOrder(
        id: json['order_id'].toString(),
        provider: json['provider']?.toString() ?? '',
        plan: json['plan']?.toString() ?? '',
        amount: (json['amount'] as num?)?.toInt(),
        status: json['status']?.toString() ?? 'pending',
        transferContent: json['transfer_content']?.toString() ?? '',
        createdAt: DateTime.tryParse(json['created_at']?.toString() ?? '')?.toLocal(),
        customerReportedAt: json['customer_reported_at'] as String?,
        bank: BankTransfer.fromJson(json['bank']),
        checkoutUrl: json['checkout_url'] as String?,
      );
}

/// `GET /api/v1/billing/overview`.
class BillingOverview {
  const BillingOverview({
    required this.account,
    required this.paymentStatus,
    required this.quota,
    required this.plans,
    required this.providers,
    required this.providerNotes,
    required this.orders,
  });

  final Account account;
  final String? paymentStatus;
  final Quota? quota;
  final List<Plan> plans;

  /// Provider key -> configured on the server.
  final Map<String, bool> providers;
  final Map<String, String> providerNotes;
  final List<PaymentOrder> orders;

  factory BillingOverview.fromJson(Map<String, dynamic> json) {
    final account = Map<String, dynamic>.from(json['account'] as Map);
    return BillingOverview(
      account: Account.fromJson(account),
      paymentStatus: account['payment_status'] as String?,
      quota: json['quota'] is Map ? Quota.fromJson(Map<String, dynamic>.from(json['quota'] as Map)) : null,
      plans: [for (final plan in (json['plans'] as List? ?? const [])) Plan.fromJson(Map<String, dynamic>.from(plan as Map))],
      providers: {
        for (final entry in (json['providers'] as Map? ?? const {}).entries) entry.key.toString(): entry.value == true,
      },
      providerNotes: {
        for (final entry in (json['provider_notes'] as Map? ?? const {}).entries)
          entry.key.toString(): entry.value.toString(),
      },
      orders: [
        for (final order in (json['orders'] as List? ?? const []))
          PaymentOrder.fromJson(Map<String, dynamic>.from(order as Map)),
      ],
    );
  }
}
