import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/core/auth/account.dart';
import 'package:vidtrans_mobile/features/jobs/domain/bulk_actions.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job_artifacts.dart';

void main() {
  test('a photo post waiting for the owner reads its summary', () {
    final job = Job.fromJson({
      'job_id': 'a1b2c3d4',
      'status': 'awaiting_input',
      'photo_post': {'image_count': 7, 'has_music': true},
    });
    expect(job.awaitsPhotoChoice, isTrue);
    expect(job.photoImageCount, 7);
    expect(job.photoHasMusic, isTrue);
    expect(jobStatusLabels['awaiting_input'], 'Chờ bạn chọn');
    expect(Job.fromJson({'job_id': 'a1b2c3d4', 'status': 'failed'}).awaitsPhotoChoice, isFalse);
  });

  test('the images zip is a downloadable artifact', () {
    final artifacts = jobArtifacts(Job.fromJson({
      'job_id': 'a1b2c3d4',
      'status': 'completed',
      'images_url': '/api/v1/jobs/a1b2c3d4/artifacts/images',
      'images_archive': 'users/u/a1b2c3d4/a1b2c3d4_images.zip',
    }));
    expect(artifacts.single.label, 'Ảnh (ZIP)');
    expect(artifacts.single.fileName, 'a1b2c3d4_images.zip');
  });

  test('the plan feature decides whether the choice is offered', () {
    Account withFeatures(Set<String>? features) =>
        Account(userId: 'u', email: 'u@x', role: 'editor', plan: 'free', features: features);
    expect(withFeatures({'photo_post'}).canUsePhotoPosts, isTrue);
    expect(withFeatures({'comic_video'}).canUsePhotoPosts, isFalse);
    expect(withFeatures(null).canUsePhotoPosts, isTrue);
  });

  test('a waiting job can be deleted, not retried, in bulk', () {
    final job = Job.fromJson({'job_id': 'a1b2c3d4', 'status': 'awaiting_input'});
    expect(BulkJobAction.delete.accepts(job), isTrue);
    expect(BulkJobAction.retry.accepts(job), isFalse);
  });
}
