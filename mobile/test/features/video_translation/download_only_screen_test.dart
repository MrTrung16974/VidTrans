import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:vidtrans_mobile/app/theme/app_theme.dart';
import 'package:vidtrans_mobile/core/auth/account.dart';
import 'package:vidtrans_mobile/core/auth/session_controller.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job_list.dart';
import 'package:vidtrans_mobile/features/jobs/presentation/providers/jobs_providers.dart';
import 'package:vidtrans_mobile/features/logo_branding/domain/branding_models.dart';
import 'package:vidtrans_mobile/features/logo_branding/presentation/providers/branding_providers.dart';
import 'package:vidtrans_mobile/features/video_translation/data/translation_repository.dart';
import 'package:vidtrans_mobile/features/video_translation/presentation/providers/translate_form_controller.dart';
import 'package:vidtrans_mobile/features/video_translation/presentation/screens/translate_screen.dart';

class _Session extends SessionController {
  @override
  Future<Account?> build() async => const Account(userId: 'u', email: 'u@t', role: 'editor', plan: 'pro');
}

class _NoJobs extends JobListController {
  @override
  Future<JobList> build() async => const JobList(jobs: [], total: 0, page: 1, totalPages: 1);
}

class _Branding extends BrandingSettingsController {
  @override
  Future<BrandingSettings> build() async => throw UnimplementedError('no branding in this test');
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  for (final width in [360.0, 412.0]) {
    testWidgets('choosing "Chỉ tải video" keeps the screen working (${width.toInt()}px)', (tester) async {
      tester.view.physicalSize = Size(width * 3, 2400);
      tester.view.devicePixelRatio = 3;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(ProviderScope(
        overrides: [
          sessionProvider.overrideWith(_Session.new),
          jobListProvider.overrideWith(_NoJobs.new),
          brandingSettingsProvider.overrideWith(_Branding.new),
          translationProvidersProvider.overrideWith((ref) async => const []),
          ttsProvidersProvider.overrideWith((ref) async => const []),
        ],
        child: MaterialApp(theme: AppTheme.light(), home: const TranslateScreen()),
      ));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull, reason: 'before choosing download-only');

      final element = tester.element(find.byType(TranslateScreen));
      final container = ProviderScope.containerOf(element);
      final form = container.read(translateFormProvider.notifier);
      form.updateConfig(container.read(translateFormProvider).config.copyWith(mode: 4));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
      expect(find.text('Nguồn video'), findsWidgets);
      expect(find.text('Cấu hình xử lý'), findsWidgets);
      // Subtitle and caption steps do not apply when only downloading.
      expect(find.text('Vị trí phụ đề'), findsNothing);
      expect(find.text('Nội dung đăng'), findsNothing);

      // Back to a translation mode: all four steps return.
      form.updateConfig(container.read(translateFormProvider).config.copyWith(mode: 1));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('Vị trí phụ đề'), findsOneWidget);
    });
  }
}
