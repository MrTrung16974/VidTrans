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
}
