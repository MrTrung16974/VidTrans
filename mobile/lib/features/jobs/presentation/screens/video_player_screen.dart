import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:video_player/video_player.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../../../core/widgets/video_controls.dart';
import '../../data/jobs_repository.dart';

/// Streams `/api/v1/jobs/{id}/artifacts/video` with the Bearer header.
class VideoPlayerScreen extends ConsumerStatefulWidget {
  const VideoPlayerScreen({super.key, required this.jobId});

  final String jobId;

  @override
  ConsumerState<VideoPlayerScreen> createState() => _VideoPlayerScreenState();
}

class _VideoPlayerScreenState extends ConsumerState<VideoPlayerScreen> {
  VideoPlayerController? _controller;
  String? _error;

  @override
  void initState() {
    super.initState();
    _open();
  }

  Future<void> _open() async {
    if (_error != null) setState(() => _error = null);
    final repo = ref.read(jobsRepositoryProvider);
    final path = '/api/v1/jobs/${widget.jobId}/artifacts/video';
    final Map<String, String> headers;
    try {
      // video_player cannot refresh an expired token; do it through Dio first.
      headers = await repo.streamHeaders(path);
    } on ApiException catch (error) {
      if (mounted) {
        setState(() => _error = error.statusCode == 404
            ? 'Video chưa sẵn sàng hoặc đã bị xóa trên máy chủ.'
            : error.message);
      }
      return;
    }
    final controller = VideoPlayerController.networkUrl(
      repo.artifactUri(path),
      httpHeaders: headers,
    );
    try {
      await controller.initialize();
      await controller.play();
      if (!mounted) return controller.dispose();
      setState(() => _controller = controller);
    } catch (error) {
      debugPrint('VideoPlayer failed for job ${widget.jobId}: $error');
      await controller.dispose();
      if (mounted) setState(() => _error = 'Không phát được video.');
    }
  }

  @override
  void dispose() {
    _controller?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final controller = _controller;
    return Scaffold(
      backgroundColor: const Color(0xFF090D16),
      appBar: AppBar(
        backgroundColor: const Color(0xFF090D16),
        foregroundColor: Colors.white,
        surfaceTintColor: Colors.transparent,
        title: const Text(
          'Trình phát Studio',
          style: TextStyle(color: Colors.white, fontSize: 16),
        ),
      ),
      body: Padding(
        padding: EdgeInsets.only(bottom: studioDockClearance(context)),
        child: Center(
          child: _error != null
              ? ErrorView(message: _error!, onRetry: _open)
              : controller == null
                  ? const CircularProgressIndicator(color: AppColors.brand)
                  : ControlledVideo(controller: controller),
        ),
      ),
    );
  }
}
