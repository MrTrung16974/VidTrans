import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/features/social_publishing/domain/social_models.dart';

void main() {
  test('parses AI variants and their grounded hashtags', () {
    final result = CaptionSuggestions.fromJson({
      'generator': 'gemini-flash',
      'fallback': false,
      'variants': [
        {
          'caption': 'Chọn hôm nay.\n\n#luachon',
          'tone': 'curious',
          'hashtags': ['#luachon'],
          'reason': 'Có hook',
        },
        {
          'caption': 'Kiên trì.\n\n#kientri',
          'tone': 'expert',
          'hashtags': ['#kientri'],
        },
      ],
    });

    expect(result.generator, 'gemini-flash');
    expect(result.fallback, isFalse);
    expect(result.variants, hasLength(2));
    expect(result.variants.first.hashtags, ['#luachon']);
  });

  test('supports an older server response with one caption', () {
    final result = CaptionSuggestions.fromJson({
      'caption': 'Nội dung #vidtrans',
      'generator': 'local-extractive-v4',
    });

    expect(result.variants.single.caption, 'Nội dung #vidtrans');
    expect(result.variants.single.tone, 'gợi ý');
  });
}
