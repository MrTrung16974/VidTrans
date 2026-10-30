import 'job.dart';

/// A result file of a job that can be downloaded with the Bearer token.
class JobArtifact {
  const JobArtifact({required this.label, required this.url, required this.fileName});

  final String label;

  /// Server path, e.g. `/api/v1/jobs/{id}/artifacts/video`.
  final String url;
  final String fileName;
}

/// Downloadable results, from the `*_url` links of job_response. File names
/// follow the stored artifact (what the server's Content-Disposition uses).
List<JobArtifact> jobArtifacts(Job job) {
  final raw = job.raw;
  String name(String field, String fallback) {
    final stored = raw[field]?.toString() ?? '';
    final base = stored.split(RegExp(r'[\\/]')).last;
    return base.isEmpty ? fallback : base;
  }

  final id = job.id;
  return [
    if (raw['video_url'] is String)
      JobArtifact(label: 'Video MP4', url: raw['video_url'] as String, fileName: name('output_video', '$id.mp4')),
    if (raw['subtitle_url'] is String)
      JobArtifact(label: 'Phụ đề SRT', url: raw['subtitle_url'] as String, fileName: name('subtitle_file', '$id.srt')),
    if (raw['translation_url'] is String)
      JobArtifact(
        label: 'Bản dịch',
        url: raw['translation_url'] as String,
        fileName: name('translation_file', '$id.translation.json'),
      ),
    if (raw['tiktok_text_url'] is String)
      JobArtifact(
        label: 'Caption TikTok',
        url: raw['tiktok_text_url'] as String,
        fileName: name('tiktok_text_file', '$id.tiktok.txt'),
      ),
    if (raw['download_all_url'] is String)
      JobArtifact(label: 'Tất cả (ZIP)', url: raw['download_all_url'] as String, fileName: '$id.artifacts.zip'),
  ];
}
