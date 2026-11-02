import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:vidtrans_mobile/app/theme/app_theme.dart';
import 'package:vidtrans_mobile/core/auth/account.dart';
import 'package:vidtrans_mobile/core/auth/session_controller.dart';
import 'package:vidtrans_mobile/features/home/data/menu_repository.dart';
import 'package:vidtrans_mobile/features/home/domain/app_menu.dart';
import 'package:vidtrans_mobile/features/home/presentation/screens/home_shell.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job_list.dart';
import 'package:vidtrans_mobile/features/jobs/presentation/providers/jobs_providers.dart';

class _NoJobs extends JobListController {
  @override
  Future<JobList> build() async =>
      const JobList(jobs: [], total: 0, page: 1, totalPages: 1);
}

class _Session extends SessionController {
  _Session(this.account);

  final Account account;

  @override
  Future<Account?> build() async => account;
}

GoRouter _router() => GoRouter(
  initialLocation: '/translate',
  routes: [
    StatefulShellRoute.indexedStack(
      builder: (_, _, shell) => HomeShell(shell: shell),
      branches: [
        for (final path in [
          '/translate',
          '/comic',
          '/jobs',
          '/social',
          '/profile',
        ])
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: path,
                builder: (_, _) => Scaffold(body: Text('page $path')),
              ),
            ],
          ),
      ],
    ),
  ],
);

AppMenu _menu(String key, String label) =>
    AppMenu(key: key, label: label, shortLabel: label);

void main() {
  Future<void> pumpShell(
    WidgetTester tester, {
    required Account account,
    AppMenuConfig? menus,
  }) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          jobListProvider.overrideWith(_NoJobs.new),
          sessionProvider.overrideWith(() => _Session(account)),
          mobileMenuProvider.overrideWith((ref) async => menus),
        ],
        child: MaterialApp.router(
          theme: AppTheme.light(),
          routerConfig: _router(),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('dock shows the server menus in their order and labels', (
    tester,
  ) async {
    await pumpShell(
      tester,
      account: const Account(
        userId: 'u',
        email: 'u@t',
        role: 'editor',
        plan: 'free',
        features: {},
      ),
      menus: AppMenuConfig(
        dockSize: 4,
        items: [
          _menu('jobs', 'Video của tôi'),
          _menu('translate', 'Dịch'),
          _menu('upgrade', 'Nâng cấp'),
          _menu('profile', 'Tài khoản'),
          _menu('future-screen', 'Không biết'),
        ],
      ),
    );
    expect(find.text('Video của tôi'), findsOneWidget);
    expect(find.text('Không biết'), findsNothing);
    expect(find.text('Truyện'), findsNothing);
    expect(find.text('Đăng bài'), findsNothing);
    final dx = [
      for (final label in ['Video của tôi', 'Dịch', 'Nâng cấp', 'Tài khoản'])
        tester.getCenter(find.text(label)).dx,
    ];
    expect(dx, orderedEquals([...dx]..sort()));

    await tester.tap(find.text('Tài khoản'));
    await tester.pumpAndSettle();
    expect(find.text('page /profile'), findsOneWidget);
  });

  testWidgets('dock keeps only dockSize entries', (tester) async {
    await pumpShell(
      tester,
      account: const Account(
        userId: 'u',
        email: 'u@t',
        role: 'editor',
        plan: 'pro',
      ),
      menus: AppMenuConfig(
        dockSize: 4,
        items: [
          _menu('translate', 'Dịch'),
          _menu('comic', 'Truyện'),
          _menu('jobs', 'Tiến trình'),
          _menu('social', 'Đăng bài'),
          _menu('upgrade', 'Nâng cấp'),
        ],
      ),
    );
    expect(find.text('Đăng bài'), findsOneWidget);
    expect(find.text('Nâng cấp'), findsNothing);
  });

  testWidgets('without server menus a free plan still fills four tabs', (
    tester,
  ) async {
    await pumpShell(
      tester,
      account: const Account(
        userId: 'u',
        email: 'u@t',
        role: 'editor',
        plan: 'trial',
        features: {},
      ),
    );
    for (final label in ['Dịch', 'Tiến trình', 'Nâng cấp', 'Tài khoản']) {
      expect(find.text(label), findsOneWidget, reason: label);
    }
    expect(find.text('Truyện'), findsNothing);
  });
}
