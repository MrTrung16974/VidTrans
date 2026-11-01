import '../../../core/websocket/job_events.dart';
import 'job.dart';

class JobList {
  const JobList({
    required this.jobs,
    required this.total,
    required this.page,
    required this.totalPages,
    this.limit = 10,
    this.counts,
  });

  final List<Job> jobs;
  final int total;
  final int page;
  final int totalPages;
  final int limit;

  /// Jobs per status across every page (`counts` of `GET /api/v1/jobs`), or null.
  final Map<String, int>? counts;

  bool get hasMore => page < totalPages;

  /// 0-based index of the first job on this page.
  int get startIndex => (page - 1) * limit;

  int countOf(Iterable<String> statuses) => counts == null
      ? jobs.where((job) => statuses.contains(job.status)).length
      : statuses.fold(0, (sum, status) => sum + (counts![status] ?? 0));

  int get activeCount => countOf(const ['queued', 'scheduled', 'processing', 'cancelling']);

  /// All jobs of every status; the page total when the server sends no counts.
  int get allCount => counts == null ? total : counts!.values.fold(0, (sum, value) => sum + value);

  factory JobList.fromJson(Map<String, dynamic> json) => JobList(
        jobs: [for (final item in json['items'] as List) Job.fromJson(Map<String, dynamic>.from(item as Map))],
        total: (json['total'] as num).toInt(),
        page: (json['page'] as num).toInt(),
        totalPages: (json['total_pages'] as num).toInt(),
        limit: (json['limit'] as num?)?.toInt() ?? 10,
        counts: json['counts'] is Map
            ? {
                for (final entry in (json['counts'] as Map).entries)
                  if (entry.value is num) entry.key.toString(): (entry.value as num).toInt(),
              }
            : null,
      );

  JobList copyWith({List<Job>? jobs, int? page, int? totalPages, int? total}) => JobList(
        jobs: jobs ?? this.jobs,
        total: total ?? this.total,
        page: page ?? this.page,
        totalPages: totalPages ?? this.totalPages,
        limit: limit,
        counts: counts,
      );
}

/// Patches the visible list in place, mirroring applyJobChanges in
/// web/js/job-events.js. `reload` is true when ordering, filtering or totals
/// may have changed and the list should be fetched again.
({List<Job> jobs, bool reload}) applyJobChanges(List<Job> jobs, JobChanges changes,
    {String? statusFilter}) {
  if (changes.resync) return (jobs: jobs, reload: true);
  var reload = false;
  final removed = changes.removed.toSet();
  var next = removed.isEmpty ? [...jobs] : jobs.where((job) => !removed.contains(job.id)).toList();
  final indexById = {for (var i = 0; i < next.length; i++) next[i].id: i};
  for (final item in changes.items) {
    final index = indexById[item['job_id']?.toString()];
    if (index == null) {
      reload = true; // a new job: let the server apply filters and ordering
      continue;
    }
    final job = Job.fromJson(item);
    if (statusFilter != null && statusFilter.isNotEmpty && job.status != statusFilter) reload = true;
    next[index] = job;
  }
  final positions = changes.queuePositions;
  if (positions != null) {
    next = [
      for (final job in next)
        positions[job.id] == job.queuePosition ? job : job.withQueuePosition(positions[job.id]),
    ];
  }
  return (jobs: next, reload: reload);
}
