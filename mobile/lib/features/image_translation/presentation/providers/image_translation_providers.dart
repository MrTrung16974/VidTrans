import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/api/providers.dart';
import '../../data/block_translators.dart';
import '../../data/ocr_service.dart';
import '../../domain/image_translation_pipeline.dart';

final ocrServiceProvider = Provider<OcrService>((ref) {
  final service = OcrService();
  ref.onDispose(service.close);
  return service;
});

final languageDetectorProvider = Provider<BlockLanguageDetector>((ref) {
  final detector = BlockLanguageDetector();
  ref.onDispose(detector.close);
  return detector;
});

final offlineTranslatorProvider = Provider<OfflineBlockTranslator>(
  (ref) => OfflineBlockTranslator(),
);

/// The server's AI default ("Chất lượng").
final onlineTranslatorProvider = Provider<OnlineBlockTranslator>(
  (ref) => OnlineBlockTranslator(ref.watch(apiClientProvider)),
);

/// "Nhanh": [fastOnlineProvider] on the server.
final fastOnlineTranslatorProvider = Provider<OnlineBlockTranslator>(
  (ref) => OnlineBlockTranslator(
    ref.watch(apiClientProvider),
    provider: fastOnlineProvider,
  ),
);

final imageTranslationPipelineProvider = Provider<ImageTranslationPipeline>((
  ref,
) {
  final ocr = ref.watch(ocrServiceProvider);
  final detector = ref.watch(languageDetectorProvider);
  return ImageTranslationPipeline(ocr: ocr.recognize, detect: detector.detect);
});
