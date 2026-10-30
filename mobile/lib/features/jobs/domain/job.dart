/// One job as returned by `/api/v1/jobs` and `/api/v1/jobs/{job_id}`
/// (job_response on the backend). Unknown fields stay available in [raw].
class Job {
  const Job(this.raw);

  final Map<String, dynamic> raw;

  String get id => raw['job_id'].toString();
  String get status => raw['status'] as String? ?? 'queued';
  String get title =>
      (raw['filename'] as String?) ?? (raw['source_url'] as String?) ?? 'Job ${id.substring(0, 8)}';
  String? get step => raw['step'] as String?;
  String? get stepDetail => raw['step_detail'] as String?;
  String? get error => raw['error'] as String?;
  int? get queuePosition => (raw['queue_position'] as num?)?.toInt();
  DateTime? get createdAt => DateTime.tryParse(raw['created_at']?.toString() ?? '')?.toLocal();

  /// Progress in 0..1. Every pipeline (translation, logo_branding,
  /// comic_video) stores a 0..1 fraction; the web shows `progress * 100`.
  double? get progress {
    final value = raw['progress'];
    if (value is! num) return null;
    return value.clamp(0, 1).toDouble();
  }

  String? get videoUrl => raw['video_url'] as String?;
  String? get subtitleUrl => raw['subtitle_url'] as String?;

  bool get isActive => const {'queued', 'scheduled', 'processing', 'cancelling'}.contains(status);
  bool get canCancel => status == 'queued' || status == 'processing';
  bool get canRetry => status == 'failed' || status == 'cancelled';

  Job withQueuePosition(int? position) {
    final next = Map<String, dynamic>.from(raw);
    if (position == null || position == 0) {
      next.remove('queue_position');
    } else {
      next['queue_position'] = position;
    }
    return Job(next);
  }

  factory Job.fromJson(Map<String, dynamic> json) => Job(json);
}

const jobStatusLabels = {
  'queued': 'Đang chờ',
  'scheduled': 'Đã lên lịch',
  'processing': 'Đang xử lý',
  'cancelling': 'Đang hủy',
  'completed': 'Hoàn thành',
  'failed': 'Lỗi',
  'cancelled': 'Đã hủy',
};
