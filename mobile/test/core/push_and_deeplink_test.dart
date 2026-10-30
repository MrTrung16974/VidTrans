import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/core/deeplink/deep_link_service.dart';
import 'package:vidtrans_mobile/core/push/firebase_config.dart';
import 'package:vidtrans_mobile/core/push/push_service.dart';

void main() {
  group('DeepLink.parse', () {
    test('OAuth success and failure', () {
      final ok = DeepLink.parse(Uri.parse('vidtrans://oauth?tiktok=connected'));
      expect(ok, isA<OAuthResult>());
      expect((ok! as OAuthResult).platform, 'tiktok');
      expect((ok as OAuthResult).connected, isTrue);

      final failed = DeepLink.parse(Uri.parse('vidtrans://oauth?youtube_error=access_denied')) as OAuthResult;
      expect(failed.platform, 'youtube');
      expect(failed.error, 'access_denied');
      expect(failed.connected, isFalse);
    });

    test('job links', () {
      final link = DeepLink.parse(Uri.parse('vidtrans://jobs/abc123'));
      expect(link, isA<JobLink>());
      expect((link! as JobLink).jobId, 'abc123');
    });

    test('ignores unknown links', () {
      expect(DeepLink.parse(Uri.parse('https://vidtrans.cloud/jobs/abc')), isNull);
      expect(DeepLink.parse(Uri.parse('vidtrans://jobs')), isNull);
      expect(DeepLink.parse(Uri.parse('vidtrans://oauth')), isNull);
      expect(DeepLink.parse(Uri.parse('vidtrans://settings')), isNull);
    });
  });

  group('JobPush.fromMessage', () {
    test('reads the job status payload sent by the backend', () {
      final push = JobPush.fromMessage(const RemoteMessage(
        data: {'type': 'job_status', 'job_id': 'j1', 'status': 'completed'},
        notification: RemoteNotification(title: 'Đã xử lý xong', body: 'clip.mp4 đã sẵn sàng.'),
      ));
      expect(push!.jobId, 'j1');
      expect(push.status, 'completed');
      expect(push.body, 'clip.mp4 đã sẵn sàng.');
    });

    test('ignores other messages', () {
      expect(JobPush.fromMessage(const RemoteMessage(data: {'type': 'promo'})), isNull);
      expect(JobPush.fromMessage(const RemoteMessage(data: {'type': 'job_status'})), isNull);
    });
  });

  test('push stays off without FIREBASE_* dart-defines', () {
    expect(FirebaseConfig.current, isNull);
  });
}
