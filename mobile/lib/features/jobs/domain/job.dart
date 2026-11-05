/// `error_code` of a job that failed because its video has no speech to translate.
const noSpeechErrorCode = 'no_speech';

/// One job as returned by `/api/v1/jobs` and `/api/v1/jobs/{job_id}`
/// (job_response on the backend). Unknown fields stay available in [raw].
class Job {
  const Job(this.raw);

  final Map<String, dynamic> raw;

  String get id => raw['job_id'].toString();
  String get status => raw['status'] as String? ?? 'queued';
  String get title =>
      (raw['filename'] as String?) ??
      (raw['source_url'] as String?) ??
      'Job ${id.substring(0, 8)}';
  String? get step => raw['step'] as String?;
  String? get stepDetail => raw['step_detail'] as String?;
  String? get error => raw['error'] as String?;
  int? get queuePosition => (raw['queue_position'] as num?)?.toInt();
  DateTime? get createdAt =>
      DateTime.tryParse(raw['created_at']?.toString() ?? '')?.toLocal();

  /// Progress in 0..1. Every pipeline (translation, logo_branding,
  /// comic_video) stores a 0..1 fraction; the web shows `progress * 100`.
  double? get progress {
    final value = raw['progress'];
    if (value is! num) return null;
    return value.clamp(0, 1).toDouble();
  }

  /// Vietnamese label for [step], as in `stepLabels` of web/js/app.js.
  String? get stepLabel {
    final value = step;
    if (value == null || value.isEmpty) return null;
    return jobStepLabels[value] ?? value;
  }

  /// `translation` (default), `logo_branding`, `comic_video`, `publish_tiktok`.
  String get action =>
      _string('job_action') ?? _string('job_type') ?? 'translation';
  String get actionLabel => jobActionLabels[action] ?? 'Dịch video';

  String? get sourceUrl => _string('source_url');
  String? get batchId => _string('batch_id');
  int? get mode => (raw['mode'] as num?)?.toInt();
  String? get sourceLanguage => _string('source_language');
  String? get targetLanguage => _string('target_language');
  String? get subtitleSource => _string('subtitle_source');
  String? get translationProvider => _string('translation_provider');
  String? get ttsProvider {
    final routing = raw['voice_routing'];
    final nested = routing is Map ? routing['tts_provider']?.toString() : null;
    return (nested?.isNotEmpty ?? false) ? nested : _string('tts_provider');
  }

  String? get ownerName =>
      _string('owner_display_name') ?? _string('owner_email');
  String? get tiktokStatus => _string('tiktok_publish_status');
  String? get tiktokError => _string('tiktok_publish_error');
  DateTime? get tiktokPublishAt => _date('tiktok_publish_at');

  DateTime? get startedAt => _date('started_at');
  DateTime? get finishedAt => _date('finished_at') ?? _date('completed_at');
  DateTime? get updatedAt => _date('updated_at');

  /// Time spent processing: until now while running, until finish otherwise.
  Duration? get elapsed {
    final start = startedAt;
    if (start == null) return null;
    final end = finishedAt ?? (isActive ? DateTime.now() : null);
    if (end == null || end.isBefore(start)) return null;
    return end.difference(start);
  }

  String? _string(String key) {
    final value = raw[key];
    if (value == null) return null;
    final text = value.toString().trim();
    return text.isEmpty ? null : text;
  }

  DateTime? _date(String key) =>
      DateTime.tryParse(raw[key]?.toString() ?? '')?.toLocal();

  String? get videoUrl => raw['video_url'] as String?;
  String? get subtitleUrl => raw['subtitle_url'] as String?;

  bool get isActive => const {
    'queued',
    'scheduled',
    'processing',
    'pausing',
    'cancelling',
  }.contains(status);
  bool get canPause =>
      const {'queued', 'scheduled', 'processing'}.contains(status);
  bool get canResume => status == 'paused';
  bool get canCancel =>
      const {'queued', 'scheduled', 'processing', 'paused'}.contains(status);
  bool get canRetry => status == 'failed' || status == 'cancelled';

  /// `error_code` of a failed job (see NoSpeechError on the backend).
  String? get errorCode => raw['error_code'] as String?;

  /// Failed because the video holds no speech: it can still be downloaded as is.
  bool get canDownloadOnly =>
      status == 'failed' && errorCode == noSpeechErrorCode;

  /// A photo post waiting for the owner: download its images or make a slideshow.
  bool get awaitsPhotoChoice => status == 'awaiting_input';

  Map<String, dynamic> get _photoPost => raw['photo_post'] is Map
      ? Map<String, dynamic>.from(raw['photo_post'] as Map)
      : const {};

  int get photoImageCount => (_photoPost['image_count'] as num?)?.toInt() ?? 0;

  bool get photoHasMusic => _photoPost['has_music'] == true;

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
  'pausing': 'Đang tạm dừng',
  'paused': 'Đã tạm dừng',
  'cancelling': 'Đang hủy',
  'completed': 'Hoàn thành',
  'failed': 'Lỗi',
  'cancelled': 'Đã hủy',
  'awaiting_input': 'Chờ bạn chọn',
};

/// Same wording as `stepLabels` in web/js/app.js.
const jobStepLabels = {
  'queued': 'Đang xếp hàng',
  'starting': 'Đang khởi động',
  'downloading-source': 'Đang tải video nguồn',
  'source-ready': 'Nguồn đã sẵn sàng',
  'extracting-subtitles': 'Đang đọc phụ đề nguồn',
  'transcribing': 'Đang nhận diện giọng nói',
  'translating': 'Đang dịch phụ đề',
  'summarizing': 'Đang viết nội dung TikTok',
  'routing-voices': 'Đang chọn giọng',
  'tts': 'Đang tạo giọng đọc',
  'mixing-audio': 'Đang trộn âm thanh',
  'rendering': 'Đang dựng video',
  'publishing-tiktok': 'Đang đăng lên TikTok',
  'waiting-tiktok-publish': 'Đang chờ lịch đăng TikTok',
  'completed': 'Đã hoàn tất',
  'failed': 'Xử lý thất bại',
  'cancelled': 'Đã hủy',
  'cancelling': 'Đang dừng an toàn',
  'pausing': 'Đang tạm dừng an toàn',
  'paused': 'Đã tạm dừng',
  'photo-choice': 'Bài đăng ảnh: chờ bạn chọn',
  'packing-images': 'Đang gom ảnh để tải về',
  'rendering-slideshow': 'Đang dựng video ghép ảnh',
  'comic_storyboard': 'Đang chia cảnh truyện',
  'comic_references': 'Đang vẽ nhân vật tham chiếu',
  'comic_images': 'Đang vẽ minh họa',
  'comic_voice': 'Đang lồng tiếng truyện',
  'comic_clips': 'Đang dựng clip cảnh',
  'comic_export': 'Đang xuất video truyện',
  'storyboard_ready': 'Storyboard chờ duyệt',
};

const jobActionLabels = {
  'translation': 'Dịch video',
  'logo_branding': 'Gắn logo',
  'comic_video': 'Video truyện',
  'publish_tiktok': 'Đăng TikTok',
};

const jobModeLabels = {
  1: 'Chỉ phụ đề',
  2: 'Phụ đề + lồng tiếng',
  3: 'Phụ đề + lồng tiếng + nhạc nền',
};
