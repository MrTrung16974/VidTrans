import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job.dart';

void main() {
  // backend/main.py (translation), branding_service.py and comic_service.py all
  // store a 0..1 fraction; web/js renders Math.round(progress * 100).
  for (final action in [null, 'logo_branding', 'comic_video']) {
    test('progress is a 0..1 fraction for job_action=$action', () {
      Job job(Object? progress) =>
          Job.fromJson({'job_id': 'j', 'status': 'processing', 'job_action': ?action, 'progress': progress});
      expect(job(0.55).progress, 0.55);
      expect(job(1.0).progress, 1.0);
      expect(job(0).progress, 0.0);
      expect(job(null).progress, isNull);
    });
  }
}
