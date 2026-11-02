import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/core/auth/account.dart';

Account _account({
  String role = 'editor',
  String? plan,
  Set<String>? features,
}) => Account(
  userId: 'u1',
  email: 'a@b.c',
  role: role,
  plan: plan,
  features: features,
);

void main() {
  test('server features decide each tab', () {
    final free = _account(plan: 'free', features: {});
    expect(free.canUseComics, isFalse);
    expect(free.canPublishSocial, isFalse);

    final socialOnly = _account(
      plan: 'free',
      features: {PlanFeature.socialPublishing},
    );
    expect(socialOnly.canUseComics, isFalse);
    expect(socialOnly.canPublishSocial, isTrue);
  });

  test('features are read from /me', () {
    final account = Account.fromJson({
      'user_id': 'u1',
      'role': 'editor',
      'plan': 'trial',
      'features': ['comic_video'],
    });
    expect(account.canUseComics, isTrue);
    expect(account.canPublishSocial, isFalse);
  });

  test(
    'older servers without features: trial, free or missing plan get nothing',
    () {
      for (final plan in ['free', 'trial', null]) {
        expect(_account(plan: plan).canUseComics, isFalse, reason: '$plan');
        expect(_account(plan: plan).canPublishSocial, isFalse, reason: '$plan');
      }
      for (final plan in ['creator', 'pro', 'enterprise', ' Pro ']) {
        expect(_account(plan: plan).canUseComics, isTrue, reason: plan);
      }
    },
  );

  test('owners and admins are never limited', () {
    expect(
      _account(role: 'owner', plan: 'free', features: {}).canUseComics,
      isTrue,
    );
    expect(
      _account(role: 'admin', plan: 'trial', features: {}).canPublishSocial,
      isTrue,
    );
  });
}
