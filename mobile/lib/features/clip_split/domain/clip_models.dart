/// "Cắt clip": cut a finished (translated/dubbed) video into several clips.
/// Mirrors backend/application/clip_routes.py (`/api/v1/clips`).
library;

/// How a clip is framed. Keys are the backend's `pipeline/clip_planning.FRAMES`.
enum ClipFrame {
  original('original', 'Giữ khung gốc'),
  verticalBlur('vertical_blur', 'Dọc 9:16 (nền mờ)'),
  verticalCrop('vertical_crop', 'Dọc 9:16 (crop giữa)');

  const ClipFrame(this.key, this.label);

  final String key;
  final String label;

  static ClipFrame parse(Object? value) =>
      ClipFrame.values.firstWhere((frame) => frame.key == value, orElse: () => ClipFrame.original);
}

/// `1:23.4`, `1:02:05.0` (hours only when needed).
String formatClipTime(double seconds) {
  final value = seconds.isFinite && seconds > 0 ? seconds : 0.0;
  final tenths = (value * 10).round();
  final hours = tenths ~/ 36000;
  final minutes = (tenths ~/ 600) % 60;
  final secs = (tenths % 600) / 10;
  final secText = secs.toStringAsFixed(1).padLeft(4, '0');
  return hours > 0 ? '$hours:${minutes.toString().padLeft(2, '0')}:$secText' : '${tenths ~/ 600}:$secText';
}

/// `1:23.5`, `1:02:05` or plain seconds `83.5` (a comma works too); null when unreadable.
double? parseClipTime(String text) {
  final parts = text.trim().replaceAll(',', '.').split(':');
  if (parts.isEmpty || parts.length > 3) return null;
  var total = 0.0;
  for (final part in parts) {
    if (!RegExp(r'^\d+(\.\d+)?$').hasMatch(part)) return null;
    total = total * 60 + double.parse(part);
  }
  return total;
}

class ClipLimits {
  const ClipLimits({this.minClipSeconds = 5, this.maxClipSeconds = 600, this.maxClips = 30});

  final double minClipSeconds;
  final double maxClipSeconds;
  final int maxClips;

  factory ClipLimits.fromJson(Map<String, dynamic>? json) => ClipLimits(
        minClipSeconds: (json?['min_clip_seconds'] as num?)?.toDouble() ?? 5,
        maxClipSeconds: (json?['max_clip_seconds'] as num?)?.toDouble() ?? 600,
        maxClips: (json?['max_clips'] as num?)?.toInt() ?? 30,
      );
}

/// One finished job in `GET /api/v1/clips/sources`.
class ClipSourceSummary {
  const ClipSourceSummary({
    required this.jobId,
    required this.filename,
    this.duration,
    this.hasSubtitles = false,
    this.completedAt,
  });

  final String jobId;
  final String filename;
  final double? duration;
  final bool hasSubtitles;
  final DateTime? completedAt;

  factory ClipSourceSummary.fromJson(Map<String, dynamic> json) => ClipSourceSummary(
        jobId: json['job_id'].toString(),
        filename: json['filename']?.toString() ?? json['job_id'].toString(),
        duration: (json['duration'] as num?)?.toDouble(),
        hasSubtitles: json['has_subtitles'] == true,
        completedAt: DateTime.tryParse(json['completed_at']?.toString() ?? '')?.toLocal(),
      );
}

class ClipSourcePage {
  const ClipSourcePage(this.items, this.total);

  final List<ClipSourceSummary> items;
  final int total;

  factory ClipSourcePage.fromJson(Map<String, dynamic> json) => ClipSourcePage(
        [
          for (final item in json['items'] as List? ?? const [])
            if (item is Map) ClipSourceSummary.fromJson(Map<String, dynamic>.from(item)),
        ],
        (json['total'] as num?)?.toInt() ?? 0,
      );
}

class ClipCue {
  const ClipCue(this.start, this.end, this.text);

  final double start;
  final double end;
  final String text;
}

/// `GET /api/v1/clips/sources/{job_id}`: what the plan editor needs.
class ClipSource {
  const ClipSource({
    required this.jobId,
    required this.filename,
    required this.duration,
    required this.videoUrl,
    this.cues = const [],
    this.canCrop = false,
    this.aiAvailable = false,
    this.aiReason,
    this.limits = const ClipLimits(),
  });

  final String jobId;
  final String filename;
  final double duration;
  final String videoUrl;
  final List<ClipCue> cues;

  /// The 9:16 crop needs the source before subtitles were burned in.
  final bool canCrop;
  final bool aiAvailable;

  /// Why AI suggestions are locked (plan, server setup, no subtitles).
  final String? aiReason;
  final ClipLimits limits;

  bool allows(ClipFrame frame) => frame != ClipFrame.verticalCrop || canCrop;

  factory ClipSource.fromJson(Map<String, dynamic> json) {
    final ai = json['ai_suggest'] is Map ? Map<String, dynamic>.from(json['ai_suggest'] as Map) : const {};
    return ClipSource(
      jobId: json['job_id'].toString(),
      filename: json['filename']?.toString() ?? json['job_id'].toString(),
      duration: (json['duration'] as num?)?.toDouble() ?? 0,
      videoUrl: json['video_url']?.toString() ?? '/api/v1/jobs/${json['job_id']}/artifacts/video',
      cues: [
        for (final cue in json['cues'] as List? ?? const [])
          if (cue is Map)
            ClipCue((cue['start'] as num).toDouble(), (cue['end'] as num).toDouble(), cue['text']?.toString() ?? ''),
      ],
      canCrop: json['can_crop'] == true,
      aiAvailable: ai['available'] == true,
      aiReason: ai['reason']?.toString(),
      limits: ClipLimits.fromJson(json['limits'] is Map ? Map<String, dynamic>.from(json['limits'] as Map) : null),
    );
  }
}

/// One clip of the plan being edited.
class ClipDraft {
  const ClipDraft({required this.start, required this.end, this.title = '', this.frame = ClipFrame.original});

  final double start;
  final double end;
  final String title;
  final ClipFrame frame;

  double get duration => end - start;

  ClipDraft copyWith({double? start, double? end, String? title, ClipFrame? frame}) => ClipDraft(
        start: start ?? this.start,
        end: end ?? this.end,
        title: title ?? this.title,
        frame: frame ?? this.frame,
      );

  factory ClipDraft.fromJson(Map<String, dynamic> json) => ClipDraft(
        start: (json['start'] as num).toDouble(),
        end: (json['end'] as num).toDouble(),
        title: json['title']?.toString() ?? '',
        frame: ClipFrame.parse(json['frame']),
      );

  Map<String, dynamic> toJson() => {'start': start, 'end': end, 'title': title.trim(), 'frame': frame.key};

  /// What is wrong with this clip for [source], or null. The server checks again
  /// (and snaps to sentence gaps); this only spares a round trip.
  String? problem(ClipSource source) {
    if (end <= start) return 'Kết thúc phải sau bắt đầu';
    if (duration < source.limits.minClipSeconds) return 'Ngắn hơn ${source.limits.minClipSeconds.round()} giây';
    if (duration > source.limits.maxClipSeconds) return 'Dài hơn ${(source.limits.maxClipSeconds / 60).round()} phút';
    if (start < 0 || end > source.duration + 0.05) return 'Nằm ngoài độ dài video';
    if (!source.allows(frame)) return 'Không crop giữa được video này';
    return null;
  }
}

/// Whether [clips] can be sent: something to cut, nothing invalid, within the clip limit.
bool planReady(List<ClipDraft> clips, ClipSource source) =>
    clips.isNotEmpty && clips.length <= source.limits.maxClips && clips.every((clip) => clip.problem(source) == null);
