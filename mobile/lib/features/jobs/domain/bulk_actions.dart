import 'job.dart';

/// Actions on several selected jobs, with the rules of the web job list
/// (`eligibleJobs` in web/js/app.js).
enum BulkJobAction {
  cancel('Hủy', 'hủy'),
  retry('Chạy lại', 'chạy lại'),
  delete('Xóa', 'xóa');

  const BulkJobAction(this.label, this.verb);

  final String label;
  final String verb;

  bool accepts(Job job) => switch (this) {
    cancel => const {'queued', 'scheduled', 'processing'}.contains(job.status),
    retry ||
    delete => const {'completed', 'failed', 'cancelled'}.contains(job.status),
  };

  List<Job> eligible(Iterable<Job> jobs) => jobs.where(accepts).toList();
}

class BulkJobResult {
  const BulkJobResult(
    this.action, {
    required this.succeeded,
    this.errors = const [],
  });

  final BulkJobAction action;
  final List<String> succeeded;
  final List<String> errors;

  /// "Đã xóa 3 job; 1 job lỗi: …" (first error), like the web toast.
  String get message {
    final failed = errors.isEmpty
        ? ''
        : '; ${errors.length} job lỗi: ${errors.first}';
    return 'Đã ${action.verb} ${succeeded.length} job$failed';
  }
}
