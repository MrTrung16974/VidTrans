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
  Future<Account?> build() async =>
      const Account(userId: 'u', email: 'u@t', role: 'editor', plan: 'pro');
}

class _NoJobs extends JobListController {
  @override
  Future<JobList> build() async =>
      const JobList(jobs: [], total: 0, page: 1, totalPages: 1);
}

class _Branding extends BrandingSettingsController {
  @override
  Future<BrandingSettings> build() async =>
      throw UnimplementedError('no branding in this test');
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  for (final width in [360.0, 412.0]) {
    testWidgets(
      'choosing "Chỉ tải video" keeps the screen working (${width.toInt()}px)',
      (tester) async {
        tester.view.physicalSize = Size(width * 3, 2400);
        tester.view.devicePixelRatio = 3;
        addTearDown(tester.view.reset);
        await tester.pumpWidget(
          ProviderScope(
            overrides: [
              sessionProvider.overrideWith(_Session.new),
              jobListProvider.overrideWith(_NoJobs.new),
              brandingSettingsProvider.overrideWith(_Branding.new),
              translationProvidersProvider.overrideWith(
                (ref) async => const [],
              ),
              ttsProvidersProvider.overrideWith((ref) async => const []),
            ],
            child: MaterialApp(
              theme: AppTheme.light(),
              home: const TranslateScreen(),
            ),
          ),
        );
        await tester.pumpAndSettle();
        expect(
          tester.takeException(),
          isNull,
          reason: 'before choosing download-only',
        );
        final element = tester.element(find.byType(TranslateScreen));
        final container = ProviderScope.containerOf(element);
        final form = container.read(translateFormProvider.notifier);
        const quickBarKey = Key('translate-quick-submit-bar');

        // The floating bar must not cover the source picker without a source.
        expect(find.byKey(quickBarKey), findsNothing);

        form.updateConfig(
          container.read(translateFormProvider).config.copyWith(mode: 4),
        );
        await tester.pumpAndSettle();

        expect(tester.takeException(), isNull);
        expect(find.text('Nguồn video'), findsWidgets);
        expect(find.text('Cấu hình xử lý'), findsWidgets);
        // Subtitle and caption steps do not apply when only downloading.
        expect(find.text('Vị trí phụ đề'), findsNothing);
        expect(find.text('Nội dung đăng'), findsNothing);

        // Back to a translation mode: all four steps return.
        form.updateConfig(
          container.read(translateFormProvider).config.copyWith(mode: 1),
        );
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        expect(find.text('Vị trí phụ đề'), findsOneWidget);
      },
    );
  }

  testWidgets('quick submit bar follows valid sources', (tester) async {
    tester.view.physicalSize = const Size(1236, 2400);
    tester.view.devicePixelRatio = 3;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          sessionProvider.overrideWith(_Session.new),
          jobListProvider.overrideWith(_NoJobs.new),
          brandingSettingsProvider.overrideWith(_Branding.new),
          translationProvidersProvider.overrideWith((ref) async => const []),
          ttsProvidersProvider.overrideWith((ref) async => const []),
        ],
        child: MaterialApp(
          theme: AppTheme.light(),
          home: const TranslateScreen(),
        ),
      ),
    );
    await tester.pumpAndSettle();

    const quickBarKey = Key('translate-quick-submit-bar');
    expect(find.byKey(quickBarKey), findsNothing);

    final element = tester.element(find.byType(TranslateScreen));
    final container = ProviderScope.containerOf(element);
    final form = container.read(translateFormProvider.notifier);
    form.setLinks('https://www.youtube.com/watch?v=abcdefghijk');
    await tester.pumpAndSettle();

    expect(find.byKey(quickBarKey), findsOneWidget);
    expect(find.text('Bắt đầu'), findsOneWidget);

    const submitCardButtonKey = Key('translate-submit-card-button');
    const formListKey = Key('translate-form-list');
    final list = tester.widget<ListView>(find.byKey(formListKey));
    for (var i = 0; i < 12; i++) {
      list.controller!.jumpTo(list.controller!.position.maxScrollExtent);
      await tester.pump();
    }
    await tester.pumpAndSettle();
    expect(find.byKey(submitCardButtonKey), findsOneWidget);
    expect(find.byKey(quickBarKey), findsNothing);

    list.controller!.jumpTo(0);
    await tester.pumpAndSettle();
    expect(find.byKey(quickBarKey), findsOneWidget);

    form.clearLinks();
    await tester.pumpAndSettle();
    expect(find.byKey(quickBarKey), findsNothing);

    form.addFiles(const [
      PickedMedia(path: '/tmp/source.mp4', name: 'source.mp4'),
    ]);
    await tester.pumpAndSettle();
    expect(find.byKey(quickBarKey), findsOneWidget);

    form.clearFiles();
    await tester.pumpAndSettle();
    expect(find.byKey(quickBarKey), findsNothing);
  });
}
