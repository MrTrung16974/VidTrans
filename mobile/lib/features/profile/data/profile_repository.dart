import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_client.dart';
import '../../../core/api/providers.dart';
import '../../../core/auth/account.dart';
import '../domain/billing_models.dart';

final profileRepositoryProvider = Provider<ProfileRepository>((ref) => ProfileRepository(ref.watch(apiClientProvider)));

class ProfileRepository {
  ProfileRepository(this._api);

  final ApiClient _api;

  /// The server accepts only `display_name` (1..120 characters).
  Future<Account> updateDisplayName(String displayName) async => Account.fromJson(
        await _api.put<Map<String, dynamic>>('/api/v1/me', data: {'display_name': displayName.trim()}),
      );

  Future<BillingOverview> billingOverview() async =>
      BillingOverview.fromJson(await _api.get<Map<String, dynamic>>('/api/v1/billing/overview'));

  /// Form fields `plan` and `provider`, like the web payment form.
  Future<PaymentOrder> createOrder({required String plan, required String provider}) async => PaymentOrder.fromJson(
        await _api.post<Map<String, dynamic>>(
          '/api/v1/billing/orders',
          data: FormData.fromMap({'plan': plan, 'provider': provider}),
        ),
      );

  Future<PaymentOrder> order(String orderId) async =>
      PaymentOrder.fromJson(await _api.get<Map<String, dynamic>>('/api/v1/billing/orders/$orderId'));

  /// Reports a bank transfer; an admin reconciles it before the plan activates.
  Future<void> reportTransfer(String orderId) => _api.post<dynamic>('/api/v1/billing/orders/$orderId/confirm');

  Future<void> cancelOrder(String orderId) => _api.post<dynamic>('/api/v1/billing/orders/$orderId/cancel');

  Future<void> switchToFree() => _api.post<dynamic>('/api/v1/billing/switch-free');
}
