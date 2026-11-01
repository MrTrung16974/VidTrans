import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/app/theme/app_theme.dart';
import 'package:vidtrans_mobile/core/auth/account.dart';
import 'package:vidtrans_mobile/core/auth/session_controller.dart';
import 'package:vidtrans_mobile/features/home/presentation/screens/home_shell.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job_list.dart';
import 'package:vidtrans_mobile/features/jobs/presentation/providers/jobs_providers.dart';

class _FakeJobList extends JobListController {
  _FakeJobList(this.statuses);

  final List<String> statuses;

  @override
  Future<JobList> build() async => JobList(
        jobs: [for (final (i, status) in statuses.indexed) Job.fromJson({'job_id': 'job-$i-000', 'status': status})],
        total: statuses.length,
        page: 1,
        totalPages: 1,
      );
}

class _FakeSession extends SessionController {
  @override
  Future<Account?> build() async => const Account(userId: 'u', email: 'u@test', role: 'editor');
}

void main() {
  Future<void> pumpTopBar(WidgetTester tester, List<String> statuses) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        jobListProvider.overrideWith(() => _FakeJobList(statuses)),
        sessionProvider.overrideWith(_FakeSession.new),
      ],
      child: MaterialApp(theme: AppTheme.light(), home: const Scaffold(appBar: StudioTopBar())),
    ));
    // The busy arc spins forever, so advance time instead of settling.
    for (var i = 0; i < 5; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }
  }

  testWidgets('idle shows a check instead of a still spinner', (tester) async {
    await pumpTopBar(tester, ['completed', 'failed']);
    expect(find.byIcon(Icons.check_rounded), findsOneWidget);
    expect(find.byKey(const ValueKey('busy')), findsNothing);
    expect(find.byTooltip('Hệ thống sẵn sàng'), findsOneWidget);
  });

  testWidgets('running jobs show the spinner and their count', (tester) async {
    await pumpTopBar(tester, ['processing', 'queued', 'completed']);
    expect(find.byIcon(Icons.check_rounded), findsNothing);
    expect(find.byKey(const ValueKey('busy')), findsOneWidget);
    expect(find.text('2'), findsOneWidget);
    expect(find.byTooltip('2 tiến trình đang xử lý'), findsOneWidget);
  });
}
