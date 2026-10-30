import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:video_player/video_player.dart';

import '../../../../app/theme/app_theme.dart';
import '../../data/translation_repository.dart';

/// Plays the server-generated voice sample. CIT synthesizes it on the server
/// CPU, so the first play can take a while.
class VoicePreviewButton extends ConsumerStatefulWidget {
  const VoicePreviewButton({
    super.key,
    required this.provider,
    required this.voiceKey,
  });

  final String provider;
  final String voiceKey;

  @override
  ConsumerState<VoicePreviewButton> createState() => _VoicePreviewButtonState();
}

class _VoicePreviewButtonState extends ConsumerState<VoicePreviewButton> {
  VideoPlayerController? _player;
  bool _loading = false;

  @override
  void didUpdateWidget(VoicePreviewButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.provider != widget.provider ||
        oldWidget.voiceKey != widget.voiceKey) {
      _stop();
    }
  }

  @override
  void dispose() {
    _player?.dispose();
    super.dispose();
  }

  void _stop() {
    _player?.dispose();
    _player = null;
    if (mounted) setState(() {});
  }

  Future<void> _play() async {
    final repo = ref.read(translationRepositoryProvider);
    setState(() => _loading = true);
    final player = VideoPlayerController.networkUrl(
      repo.voicePreviewUri(widget.provider, widget.voiceKey),
      httpHeaders: await repo.authHeaders(),
    );
    try {
      await player.initialize();
      if (!mounted) return player.dispose();
      player.addListener(() {
        final value = player.value;
        if (value.isInitialized &&
            !value.isPlaying &&
            value.position >= value.duration &&
            _player == player) {
          _stop();
        }
      });
      await player.play();
      setState(() => _player = player);
    } catch (_) {
      await player.dispose();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text(
            'Không phát được bản nghe thử. Kiểm tra cấu hình nhà cung cấp giọng đọc.',
          ),
        ));
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final playing = _player != null;
    return Padding(
      padding: const EdgeInsets.only(left: 8),
      child: OutlinedButton.icon(
        onPressed: _loading ? null : (playing ? _stop : _play),
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(0, 44),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          backgroundColor: playing ? AppColors.ink : AppColors.surface,
          foregroundColor: playing ? Colors.white : AppColors.ink,
          side: BorderSide(
            color: playing ? AppColors.ink : AppColors.lineStrong,
          ),
        ),
        icon: _loading
            ? const SizedBox.square(
                dimension: 16,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: AppColors.brand,
                ),
              )
            : Icon(
                playing ? Icons.stop_rounded : Icons.volume_up_rounded,
                size: 18,
                color: playing ? Colors.white : AppColors.brand,
              ),
        label: Text(_loading ? 'Đang tạo…' : (playing ? 'Dừng' : 'Nghe thử')),
      ),
    );
  }
}
