import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:video_player/video_player.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/widgets/error_view.dart';
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
    final repo = ref.read(jobsRepositoryProvider);
    final controller = VideoPlayerController.networkUrl(
      repo.artifactUri('/api/v1/jobs/${widget.jobId}/artifacts/video'),
      httpHeaders: await repo.authHeaders(),
    );
    try {
      await controller.initialize();
      await controller.play();
      if (!mounted) return controller.dispose();
      setState(() => _controller = controller);
    } catch (_) {
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
      body: Center(
        child: _error != null
            ? ErrorView(message: _error!)
            : controller == null
                ? const CircularProgressIndicator(color: AppColors.brand)
                : GestureDetector(
                    onTap: () => setState(
                      () => controller.value.isPlaying
                          ? controller.pause()
                          : controller.play(),
                    ),
                    child: AspectRatio(
                      aspectRatio: controller.value.aspectRatio,
                      child: Stack(
                        alignment: Alignment.bottomCenter,
                        children: [
                          VideoPlayer(controller),
                          VideoProgressIndicator(
                            controller,
                            allowScrubbing: true,
                            colors: const VideoProgressColors(
                              playedColor: AppColors.brand,
                              bufferedColor: Colors.white38,
                              backgroundColor: Colors.white12,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
      ),
    );
  }
}
