import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/features/logo_branding/domain/branding_models.dart';

void main() {
  test('config round-trips exactly the web form keys', () {
    final json = {
      'auto_detect': false,
      'default_position': 'bottom-left',
      'logo_size': 20,
      'opacity': 0.75,
      'margin': 4.5,
      'cover_original': true,
      'cover_method': 'blur',
      'multi_logo_strategy': 'largest',
      'low_confidence_action': 'review',
      'min_confidence': 0.6,
    };
    final config = BrandingConfig.fromJson(json);
    expect(config.toJson().keys.toSet(), json.keys.toSet());
    expect(config.toJson()['opacity'], 0.75);
    // min_confidence is not editable but must be preserved on save.
    expect(config.copyWith(logoSize: 30).toJson()['min_confidence'], 0.6);
  });

  test('settings parse logo, missing template and defaults', () {
    final settings = BrandingSettings.fromJson({
      'config': {},
      'logo': {'url': '/api/v1/me/branding/logo?v=a.png', 'width': 200, 'height': 100, 'transparent': true},
      'template': null,
    });
    expect(settings.logo!.transparent, isTrue);
    expect(settings.template, isNull);
    expect(settings.config.defaultPosition, 'top-right');
  });

  test('video form fields match the web upload form', () {
    expect(brandingVideoFields(override: 'auto', rightsConfirmed: true),
        {'override': 'auto', 'rights_confirmed': 'true'});
    expect(brandingVideoFields(override: 'top-left', rightsConfirmed: false)['rights_confirmed'], 'false');
  });

  test('job progress is a 0..1 fraction and placement text follows the web', () {
    final job = BrandingJob({
      'job_id': 'j1',
      'status': 'completed',
      'progress': 1.0,
      'branding': {
        'detection': {'status': 'found', 'confidence': 0.87, 'method': 'template'},
        'placements': [
          {'source': 'detected'},
          {'source': 'tracked'},
        ],
        'cover_method': 'inpaint',
        'needs_review': true,
      },
    });
    expect(job.progress, 1.0);
    expect(job.needsReview, isTrue);
    expect(job.canDownload, isTrue);
    expect(job.placementText,
        'Tìm thấy logo theo mẫu (độ tin cậy 87%) · 2 đoạn · logo di chuyển, đã bám theo · đã khôi phục nền logo cũ');
    expect(BrandingJob({'job_id': 'j', 'branding': {'override': 'top-left'}}).placementText, 'Ép góc trên bên trái');
    expect(BrandingJob({'job_id': 'j', 'branding': {}}).placementText, 'Không nhận diện · vị trí mặc định');
  });
}
