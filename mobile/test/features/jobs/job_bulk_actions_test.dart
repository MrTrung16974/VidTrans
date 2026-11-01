import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/app/theme/app_theme.dart';
import 'package:vidtrans_mobile/core/api/api_client.dart';
import 'package:vidtrans_mobile/core/auth/account.dart';
import 'package:vidtrans_mobile/core/auth/session_controller.dart';
import 'package:vidtrans_mobile/core/errors/api_exception.dart';
import 'package:vidtrans_mobile/core/storage/token_storage.dart';
import 'package:vidtrans_mobile/features/jobs/data/jobs_repository.dart';
import 'package:vidtrans_mobile/features/jobs/domain/bulk_actions.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job_list.dart';
import 'package:vidtrans_mobile/features/jobs/presentation/providers/jobs_providers.dart';
import 'package:vidtrans_mobile/features/jobs/presentation/screens/jobs_screen.dart';

Job job(String id, String status) => Job.fromJson({
  'job_id': '$id-0000000',
  'status': status,
  'filename': '$id.mp4',
});

class _FakeRepository extends JobsRepository {
  _FakeRepository(this.jobs, {this.failing = const {}})
    : super(ApiClient(baseUrl: 'http://test', tokens: TokenStorage()));

  List<Job> jobs;
  final Set<String> failing;
  final calls = <String>[];

  Future<void> _act(String action, String jobId) async {
    calls.add('$action $jobId');
    if (failing.contains(jobId)) throw const ApiException('Không có quyền');
    if (action == 'delete') jobs = jobs.where((j) => j.id != jobId).toList();
  }

  @override
  Future<JobList> list({
    int page = 1,
    int limit = 20,
    String? status,
    String? search,
  }) async => JobList(jobs: jobs, total: jobs.length, page: 1, totalPages: 1);

  @override
  Future<void> cancel(String jobId) => _act('cancel', jobId);

  @override
  Future<void> retry(String jobId) => _act('retry', jobId);

  @override
  Future<void> delete(String jobId) => _act('delete', jobId);
}

/// The list without the live event stream (no server in tests).
class _FakeJobList extends JobListController {
  @override
  Future<JobList> build() => ref.read(jobsRepositoryProvider).list();
}

class _FakeSession extends SessionController {
  _FakeSession(this.role);

  final String role;

  @override
  Future<Account?> build() async =>
      Account(userId: 'u', email: 'u@test', role: role);
}

/// The header pulse and the running job's progress bar animate forever, so
/// pumpAndSettle never returns: advance past dialog and snackbar transitions.
Future<void> settle(WidgetTester tester) async {
  for (var i = 0; i < 8; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

void main() {
  test('actions accept the same job states as the web bulk bar', () {
    final jobs = [
      job('a', 'completed'),
      job('b', 'failed'),
      job('c', 'processing'),
      job('d', 'queued'),
    ];
    expect(BulkJobAction.cancel.eligible(jobs).map((j) => j.status), [
      'processing',
      'queued',
    ]);
    expect(BulkJobAction.retry.eligible(jobs).map((j) => j.status), [
      'completed',
      'failed',
    ]);
    expect(BulkJobAction.delete.eligible(jobs).map((j) => j.status), [
      'completed',
      'failed',
    ]);
  });

  test('one failing job does not stop the others and is reported', () async {
    final repo = _FakeRepository(
      [job('a', 'completed'), job('b', 'failed')],
      failing: {'b-0000000'},
    );
    final container = ProviderContainer(
      overrides: [
        jobsRepositoryProvider.overrideWithValue(repo),
        jobListProvider.overrideWith(_FakeJobList.new),
      ],
    );
    addTearDown(container.dispose);
    final subscription = container.listen(jobListProvider, (_, _) {});
    addTearDown(subscription.close);
    await container.read(jobListProvider.future);

    final result = await container
        .read(jobListProvider.notifier)
        .runBulk(BulkJobAction.delete, repo.jobs);

    expect(result.succeeded, ['a-0000000']);
    expect(result.message, 'Đã xóa 1 job; 1 job lỗi: Không có quyền');
    expect(container.read(jobListProvider).value!.jobs.map((j) => j.id), [
      'b-0000000',
    ]);
  });

  Future<_FakeRepository> pumpJobs(
    WidgetTester tester, {
    String role = 'editor',
    Size size = const Size(390, 844),
  }) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final repo = _FakeRepository([
      job('a', 'completed'),
      job('b', 'failed'),
      job('c', 'processing'),
    ]);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          jobsRepositoryProvider.overrideWithValue(repo),
          jobListProvider.overrideWith(_FakeJobList.new),
          sessionProvider.overrideWith(() => _FakeSession(role)),
        ],
        child: MaterialApp(theme: AppTheme.light(), home: const JobsScreen()),
      ),
    );
    await settle(tester);
    return repo;
  }

  for (final size in [const Size(360, 640), const Size(390, 844)]) {
    testWidgets(
      'long-press, select all, then delete the jobs that can be deleted (${size.width.toInt()}px)',
      (tester) async {
        final repo = await pumpJobs(tester, size: size);

        // On a short screen the list starts below the header and the metrics.
        await tester.scrollUntilVisible(
          find.text('a.mp4'),
          200,
          scrollable: find
              .byWidgetPredicate(
                (w) => w is Scrollable && w.axisDirection == AxisDirection.down,
              )
              .first,
        );
        await tester.longPress(find.text('a.mp4'));
        await settle(tester);
        expect(find.text('Đã chọn 1 tiến trình'), findsOneWidget);
        expect(find.text('Xóa (1)'), findsOneWidget);
        expect(
          tester
              .widget<OutlinedButton>(
                find.widgetWithText(OutlinedButton, 'Hủy'),
              )
              .onPressed,
          isNull,
        );

        await tester.tap(find.text('Chọn tất cả'));
        await settle(tester);
        expect(find.text('Đã chọn 3 tiến trình'), findsOneWidget);
        expect(find.text('Hủy (1)'), findsOneWidget);

        await tester.tap(find.text('Xóa (2)'));
        await settle(tester);
        expect(find.text('Xóa 2 job?'), findsOneWidget);
        await tester.tap(find.widgetWithText(FilledButton, 'Xóa'));
        await settle(tester);

        expect(repo.calls, ['delete a-0000000', 'delete b-0000000']);
        expect(find.text('Đã xóa 2 job'), findsOneWidget);
        // The running job stays selected; the deleted ones are gone from the list.
        expect(find.text('Đã chọn 1 tiến trình'), findsOneWidget);
        expect(find.text('a.mp4'), findsNothing);
      },
    );
  }

  testWidgets('back leaves selection mode instead of the screen', (
    tester,
  ) async {
    await pumpJobs(tester);
    await tester.longPress(find.text('b.mp4'));
    await settle(tester);

    await tester.binding.handlePopRoute();
    await settle(tester);

    expect(find.textContaining('Đã chọn'), findsNothing);
    expect(find.text('b.mp4'), findsOneWidget);
  });

  for (final (role, shown) in [('editor', true), ('viewer', false)]) {
    testWidgets('$role ${shown ? 'gets' : 'does not get'} the select button', (
      tester,
    ) async {
      await pumpJobs(tester, role: role);
      expect(
        find.widgetWithText(OutlinedButton, 'Chọn'),
        shown ? findsOneWidget : findsNothing,
      );
    });
  }
}
