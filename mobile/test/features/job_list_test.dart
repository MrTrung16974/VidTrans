import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/core/websocket/job_events.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job_list.dart';

void main() {
  final jobs = [
    Job.fromJson({'job_id': 'a', 'status': 'processing', 'progress': 0.4}),
    Job.fromJson({'job_id': 'b', 'status': 'queued', 'queue_position': 2}),
  ];

  test('patches known jobs and queue positions in place', () {
    final result = applyJobChanges(
      jobs,
      JobChanges.fromJson({
        'type': 'jobs',
        'items': [
          {'job_id': 'a', 'status': 'completed', 'progress': 1.0},
        ],
        'queue_positions': {'b': 1},
      }),
    );
    expect(result.reload, isFalse);
    expect(result.jobs[0].status, 'completed');
    expect(result.jobs[0].progress, 1.0);
    expect(result.jobs[1].queuePosition, 1);
  });

  test('new jobs, filter mismatches and resync ask for a reload', () {
    expect(applyJobChanges(jobs, JobChanges.fromJson({'items': [{'job_id': 'c', 'status': 'queued'}]})).reload, isTrue);
    expect(
      applyJobChanges(jobs, JobChanges.fromJson({'items': [{'job_id': 'a', 'status': 'failed'}]}),
              statusFilter: 'processing')
          .reload,
      isTrue,
    );
    expect(applyJobChanges(jobs, const JobChanges(resync: true)).reload, isTrue);
  });

  test('removed jobs disappear and dropped queue positions are cleared', () {
    final result = applyJobChanges(jobs, JobChanges.fromJson({'removed': ['a'], 'queue_positions': {}}));
    expect(result.jobs.map((j) => j.id), ['b']);
    expect(result.jobs.single.queuePosition, isNull);
  });
}
