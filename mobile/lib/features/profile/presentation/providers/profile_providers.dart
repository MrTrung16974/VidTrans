import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/profile_repository.dart';
import '../../domain/billing_models.dart';

final billingOverviewProvider = FutureProvider.autoDispose<BillingOverview>(
  (ref) => ref.read(profileRepositoryProvider).billingOverview(),
);
