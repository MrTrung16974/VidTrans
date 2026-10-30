import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/features/social_publishing/domain/social_models.dart';

void main() {
  const connected = SocialAccount(accountId: 'acc-1', connectionId: 'conn-1', name: 'Shop');
  const legacy = SocialAccount(accountId: 'open-9', name: 'open-9');

  group('buildPublishPayload', () {
    test('TikTok matches the web body: privacy option, connection id, no schedule', () {
      final payload = buildPublishPayload(
        platform: SocialPlatform.tiktok,
        account: connected,
        mode: PublishMode.directNow,
        caption: 'Xin chào #vidtrans',
        idempotencyKey: 'key-12345678',
        timezone: 'Asia/Ho_Chi_Minh',
        tiktokPrivacy: 'PUBLIC_TO_EVERYONE',
      );
      expect(payload, {
        'platform': 'TIKTOK',
        'mode': 'DIRECT_NOW',
        'caption': 'Xin chào #vidtrans',
        'timezone': 'Asia/Ho_Chi_Minh',
        'options': {'privacy_level': 'PUBLIC_TO_EVERYONE'},
        'reviewed': true,
        'idempotency_key': 'key-12345678',
        'scheduled_for': null,
        'connection_id': 'conn-1',
      });
    });

    test('YouTube sends title, privacy and category 22; schedule is UTC ISO', () {
      final payload = buildPublishPayload(
        platform: SocialPlatform.youtube,
        account: connected,
        mode: PublishMode.scheduleLocal,
        caption: '',
        idempotencyKey: 'key-12345678',
        timezone: 'UTC',
        scheduledFor: DateTime.utc(2026, 11, 1, 3, 30),
        youtubeTitle: '  Tập 1  ',
        youtubePrivacy: 'unlisted',
      );
      expect(payload['options'], {'title': 'Tập 1', 'privacy': 'unlisted', 'category_id': '22'});
      expect(payload['scheduled_for'], '2026-11-01T03:30:00.000Z');
    });

    test('legacy accounts without a connection send account_id', () {
      final payload = buildPublishPayload(
        platform: SocialPlatform.tiktok,
        account: legacy,
        mode: PublishMode.draft,
        caption: 'x',
        idempotencyKey: 'key-12345678',
        timezone: 'UTC',
      );
      expect(payload['account_id'], 'open-9');
      expect(payload.containsKey('connection_id'), isFalse);
      expect(payload['mode'], 'DRAFT');
    });
  });

  group('publishBlocker', () {
    final ready = SocialAuthStatus(configured: true, connected: true, accounts: const [connected]);

    String? blocker({
      SocialPlatform platform = SocialPlatform.facebook,
      SocialAuthStatus? auth,
      String mode = PublishMode.directNow,
      String caption = 'ok',
      bool reviewed = true,
      DateTime? scheduledFor,
      String youtubeTitle = '',
    }) =>
        publishBlocker(
          platform: platform,
          auth: auth ?? ready,
          account: (auth ?? ready).defaultAccount,
          mode: mode,
          caption: caption,
          reviewed: reviewed,
          scheduledFor: scheduledFor,
          youtubeTitle: youtubeTitle,
          now: DateTime(2026, 10, 30, 12),
        );

    test('complete form can publish', () => expect(blocker(), isNull));

    test('needs a connected account and review', () {
      expect(blocker(auth: const SocialAuthStatus(configured: true, connected: false, accounts: [])), contains('kết nối'));
      expect(blocker(auth: const SocialAuthStatus(configured: false, connected: false, accounts: [])), contains('cấu hình'));
      expect(blocker(reviewed: false), contains('xác nhận'));
    });

    test('platform rules from the web forms', () {
      expect(blocker(platform: SocialPlatform.instagram, caption: '  '), contains('Instagram'));
      expect(blocker(platform: SocialPlatform.youtube), contains('tiêu đề'));
      expect(blocker(platform: SocialPlatform.youtube, youtubeTitle: 'Tập 1'), isNull);
      final noScope = SocialAuthStatus(configured: true, connected: true, accounts: const [connected], canDirectPublish: false);
      expect(blocker(platform: SocialPlatform.tiktok, auth: noScope), contains('quyền'));
      expect(blocker(platform: SocialPlatform.tiktok, auth: noScope, mode: PublishMode.draft), isNull);
    });

    test('schedule must be chosen and in the future', () {
      expect(blocker(mode: PublishMode.scheduleLocal), contains('thời gian'));
      expect(blocker(mode: PublishMode.scheduleLocal, scheduledFor: DateTime(2026, 10, 30, 11)), contains('tương lai'));
      expect(blocker(mode: PublishMode.scheduleLocal, scheduledFor: DateTime(2026, 10, 30, 13)), isNull);
    });
  });

  test('auth status parses web account shapes and picks the active account', () {
    final status = SocialAuthStatus.fromJson({
      'configured': true,
      'connected': true,
      'connection_id': 'c2',
      'accounts': [
        {'account_id': 'a1', 'connection_id': '', 'username': 'shop1'},
        {'account_id': 'a2', 'connection_id': 'c2', 'name': 'Trang 2'},
        {'open_id': 'tt-open'},
      ],
    });
    expect(status.accounts.map((a) => a.name), ['@shop1', 'Trang 2', 'tt-open']);
    expect(status.accounts.first.connectionId, isNull);
    expect(status.defaultAccount?.accountId, 'a2');
  });

  test('attempt status helpers', () {
    final uploading = PublishAttempt.fromJson({
      'attempt_id': 'x',
      'platform': 'INSTAGRAM',
      'status': 'UPLOADING',
      'status_detail': {'percent': 42},
    });
    expect(uploading.isInFlight, isTrue);
    expect(uploading.percent, 42);
    expect(uploading.platform, SocialPlatform.instagram);
    final scheduled = PublishAttempt.fromJson({'attempt_id': 'y', 'status': 'SCHEDULED_LOCAL'});
    expect(scheduled.isInFlight, isFalse);
    expect(scheduled.canCancel, isTrue);
    expect(PublishAttempt.fromJson({'attempt_id': 'z', 'status': 'PUBLISHED'}).label, 'Đã đăng thành công');
  });
}
