import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/core/websocket/job_events.dart';

void main() {
  test('parses the Socket.IO notification event like the FCM data', () {
    final notification = JobNotification.fromJson({
      'type': 'job_status',
      'job_id': 'a1b2',
      'status': 'completed',
      'title': 'Đã xử lý xong',
      'body': 'clip.mp4 đã sẵn sàng.',
    })!;
    expect(notification.jobId, 'a1b2');
    expect(notification.status, 'completed');
    expect(notification.body, 'clip.mp4 đã sẵn sàng.');
  });

  test('ignores other events and payloads without a job', () {
    expect(JobNotification.fromJson({'type': 'billing', 'job_id': 'x'}), isNull);
    expect(JobNotification.fromJson({'type': 'job_status'}), isNull);
    expect(JobNotification.fromJson('not a map'), isNull);
  });
}
