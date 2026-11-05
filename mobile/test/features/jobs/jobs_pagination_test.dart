import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/app/theme/app_theme.dart';
import 'package:vidtrans_mobile/core/api/api_client.dart';
import 'package:vidtrans_mobile/core/auth/account.dart';
import 'package:vidtrans_mobile/core/auth/session_controller.dart';
import 'package:vidtrans_mobile/core/storage/token_storage.dart';
import 'package:vidtrans_mobile/core/websocket/job_events.dart';
import 'package:vidtrans_mobile/features/jobs/data/jobs_repository.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job_list.dart';
import 'package:vidtrans_mobile/features/jobs/presentation/providers/jobs_providers.dart';
import 'package:vidtrans_mobile/features/jobs/presentation/screens/jobs_screen.dart';
import 'package:vidtrans_mobile/features/jobs/presentation/widgets/jobs_pagination.dart';

/// 45 jobs served a page at a time, like `GET /api/v1/jobs`.
class _PagedRepository extends JobsRepository {
  _PagedRepository() : super(ApiClient(baseUrl: 'http://test', tokens: TokenStorage()));

  final requests = <({int page, int limit, String? status, String? search})>[];

  @override
  Future<JobList> list({int page = 1, int limit = 20, String? status, String? search}) async {
    requests.add((page: page, limit: limit, status: status, search: search));
    final all = [
      for (var i = 0; i < 45; i++)
        Job.fromJson({'job_id': 'job$i-0000', 'status': i < 3 ? 'processing' : 'completed', 'filename': 'video$i.mp4'}),
    ];
    final totalPages = (all.length / limit).ceil();
    // Like the server: a page past the end is answered with the last page.
    if (page > totalPages) page = totalPages;
    return JobList(
      jobs: all.skip((page - 1) * limit).take(limit).toList(),
      total: all.length,
      page: page,
      totalPages: totalPages,
      limit: limit,
      counts: const {'processing': 3, 'completed': 42},
    );
  }
}

class _NoEvents implements JobEventsService {
  @override
  Stream<JobChanges> get changes => const Stream.empty();

  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

class _FakeSession extends SessionController {
  @override
  Future<Account?> build() async => const Account(userId: 'u', email: 'u@test', role: 'editor');
}

/// Processing jobs animate forever, so advance time instead of settling.
Future<void> settle(WidgetTester tester) async {
  for (var i = 0; i < 8; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

/// Page 1 answers only when [release] completes, after the user already moved on.
class _SlowFirstPageRepository extends _PagedRepository {
  Completer<void>? hold;

  @override
  Future<JobList> list({int page = 1, int limit = 20, String? status, String? search}) async {
    final gate = hold;
    if (page == 1 && gate != null) await gate.future;
    return super.list(page: page, limit: limit, status: status, search: search);
  }
}

void main() {
  test('a slow reload of the old page does not replace the page the user moved to', () async {
    final repo = _SlowFirstPageRepository();
    final container = ProviderContainer(overrides: [
      jobsRepositoryProvider.overrideWithValue(repo),
      jobEventsProvider.overrideWithValue(_NoEvents()),
    ]);
    addTearDown(container.dispose);
    final subscription = container.listen(jobListProvider, (_, _) {});
    addTearDown(subscription.close);
    expect((await container.read(jobListProvider.future)).page, 1);

    repo.hold = Completer<void>();
    final staleReload = container.read(jobListProvider.notifier).refresh(); // e.g. a live update
    container.read(jobPageProvider.notifier).state = 2;
    expect((await container.read(jobListProvider.future)).page, 2);
    repo.hold!.complete();
    await staleReload;
    expect(container.read(jobListProvider).value!.page, 2);
  });

  test('a page the server moved back to becomes the selected page', () async {
    final repo = _PagedRepository();
    final container = ProviderContainer(overrides: [
      jobsRepositoryProvider.overrideWithValue(repo),
      jobEventsProvider.overrideWithValue(_NoEvents()),
    ]);
    addTearDown(container.dispose);
    final subscription = container.listen(jobListProvider, (_, _) {});
    addTearDown(subscription.close);
    await container.read(jobListProvider.future);
    container.read(jobPageProvider.notifier).state = 9; // only 5 pages of 10 exist
    expect((await container.read(jobListProvider.future)).page, 5);
    await Future<void>.delayed(Duration.zero);
    expect(container.read(jobPageProvider), 5);
    // Following the server's page is one more fetch of the same page, not a loop.
    await container.read(jobListProvider.future);
    expect(repo.requests.map((request) => request.page), [1, 9, 5]);
  });

  test('page numbers collapse into ellipses like the web', () {
    expect(paginationPages(1, 5), [1, 2, 3, 4, 5]);
    expect(paginationPages(2, 20), [1, 2, 3, 4, 5, null, 20]);
    expect(paginationPages(10, 20), [1, null, 9, 10, 11, null, 20]);
    expect(paginationPages(19, 20), [1, null, 16, 17, 18, 19, 20]);
  });

  testWidgets('jobs are paged on the server with counts across every page (360px)', (tester) async {
    tester.view.physicalSize = const Size(360, 640);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final repo = _PagedRepository();
    await tester.pumpWidget(ProviderScope(
      overrides: [
        jobsRepositoryProvider.overrideWithValue(repo),
        jobEventsProvider.overrideWithValue(_NoEvents()),
        sessionProvider.overrideWith(_FakeSession.new),
      ],
      child: MaterialApp(theme: AppTheme.light(), home: const JobsScreen()),
    ));
    await settle(tester);

    expect(repo.requests.last.page, 1);
    expect(repo.requests.last.limit, 10);
    // Metrics count all 45 jobs, not only the 10 on screen.
    expect(find.text('45'), findsWidgets);
    final pager = find.byType(JobsPagination);
    await tester.scrollUntilVisible(pager, 300, scrollable: find.byType(Scrollable).first);
    expect(find.textContaining('trong số'), findsOneWidget);

    await tester.tap(find.descendant(of: pager, matching: find.text('3')));
    await settle(tester);
    expect(repo.requests.last.page, 3);
    // The list jumps back to the top; the pager now reports the third page.
    await tester.scrollUntilVisible(pager, 300, scrollable: find.byType(Scrollable).first);
    expect(find.textContaining('21 – 30'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
