import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job.dart';
import 'package:vidtrans_mobile/features/jobs/domain/job_artifacts.dart';

void main() {
  test('lists only the results the server links, named like the stored files', () {
    final artifacts = jobArtifacts(Job.fromJson({
      'job_id': 'abc',
      'status': 'completed',
      'output_video': 'outputs/abc/final_video.mp4',
      'video_url': '/api/v1/jobs/abc/artifacts/video',
      'subtitle_file': 'outputs/abc/sub.srt',
      'subtitle_url': '/api/v1/jobs/abc/artifacts/subtitle',
      'download_all_url': '/api/v1/jobs/abc/download-all',
    }));
    expect(artifacts.map((a) => a.fileName), ['final_video.mp4', 'sub.srt', 'abc.artifacts.zip']);
    expect(artifacts.first.url, '/api/v1/jobs/abc/artifacts/video');
  });

  test('a running job has nothing to download', () {
    expect(jobArtifacts(Job.fromJson({'job_id': 'x', 'status': 'processing'})), isEmpty);
  });
}
