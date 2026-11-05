import 'job.dart';

/// Actions on several selected jobs, with the rules of the web job list
/// (`eligibleJobs` in web/js/app.js).
enum BulkJobAction {
  pause('Tạm dừng', 'tạm dừng'),
  resume('Tiếp tục', 'tiếp tục'),
  cancel('Hủy', 'hủy'),
  retry('Chạy lại', 'chạy lại'),
  delete('Xóa', 'xóa');

  const BulkJobAction(this.label, this.verb);

  final String label;
  final String verb;

  bool accepts(Job job) => switch (this) {
    pause => job.canPause,
    resume => job.canResume,
    cancel => const {
      'queued',
      'scheduled',
      'processing',
      'paused',
    }.contains(job.status),
    retry => const {'completed', 'failed', 'cancelled'}.contains(job.status),
    // A photo post waiting for the owner's choice can be dropped too.
    delete => const {
      'completed',
      'failed',
      'cancelled',
      'paused',
      'awaiting_input',
    }.contains(job.status),
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
