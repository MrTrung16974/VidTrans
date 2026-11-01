import 'dart:async';

import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';

import '../../app/theme/app_theme.dart';

/// A video with player controls on top: play/pause, ±10 s, a seek bar with the
/// time, and double-tap on either half to skip. The controls hide themselves
/// while the video plays; a tap brings them back.
class ControlledVideo extends StatefulWidget {
  const ControlledVideo({super.key, required this.controller});

  final VideoPlayerController controller;

  @override
  State<ControlledVideo> createState() => _ControlledVideoState();
}

class _ControlledVideoState extends State<ControlledVideo> {
  static const _skip = Duration(seconds: 10);
  static const _hideAfter = Duration(seconds: 3);

  bool _visible = true;
  Timer? _hideTimer;
  // While dragging, the bar follows the finger instead of the player.
  double? _dragMs;

  VideoPlayerController get _c => widget.controller;

  @override
  void initState() {
    super.initState();
    _c.addListener(_onTick);
    _scheduleHide();
  }

  @override
  void didUpdateWidget(ControlledVideo old) {
    super.didUpdateWidget(old);
    if (old.controller != widget.controller) {
      old.controller.removeListener(_onTick);
      widget.controller.addListener(_onTick);
    }
  }

  @override
  void dispose() {
    _hideTimer?.cancel();
    _c.removeListener(_onTick);
    super.dispose();
  }

  void _onTick() {
    if (mounted) setState(() {});
  }

  bool get _ended {
    final value = _c.value;
    return !value.isLooping &&
        value.duration > Duration.zero &&
        value.position >= value.duration - const Duration(milliseconds: 300) &&
        !value.isPlaying;
  }

  void _scheduleHide() {
    _hideTimer?.cancel();
    _hideTimer = Timer(_hideAfter, () {
      if (mounted && _c.value.isPlaying && _dragMs == null) {
        setState(() => _visible = false);
      }
    });
  }

  void _show() {
    setState(() => _visible = true);
    _scheduleHide();
  }

  void _toggleVisible() {
    if (_visible) {
      _hideTimer?.cancel();
      setState(() => _visible = false);
    } else {
      _show();
    }
  }

  Future<void> _togglePlay() async {
    if (_c.value.isPlaying) {
      await _c.pause();
      _hideTimer?.cancel();
      setState(() => _visible = true);
      return;
    }
    if (_ended) await _c.seekTo(Duration.zero);
    await _c.play();
    _show();
  }

  Future<void> _seekBy(Duration delta) async {
    final duration = _c.value.duration;
    var target = _c.value.position + delta;
    if (target < Duration.zero) target = Duration.zero;
    if (target > duration) target = duration;
    await _c.seekTo(target);
    _show();
  }

  @override
  Widget build(BuildContext context) {
    final value = _c.value;
    final durationMs = value.duration.inMilliseconds.toDouble();
    final positionMs = (_dragMs ??
            value.position.inMilliseconds.clamp(0, value.duration.inMilliseconds).toDouble())
        .clamp(0.0, durationMs > 0 ? durationMs : 0.0);

    return LayoutBuilder(
      builder: (context, constraints) => GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: _toggleVisible,
        onDoubleTapDown: (details) => _seekBy(
          details.localPosition.dx < constraints.maxWidth / 2 ? -_skip : _skip,
        ),
        onDoubleTap: () {},
        child: Stack(
          fit: StackFit.expand,
          children: [
            Center(
              child: AspectRatio(
                aspectRatio: value.aspectRatio,
                child: VideoPlayer(_c),
              ),
            ),
            if (value.isBuffering)
              const Center(
                child: CircularProgressIndicator(color: AppColors.brand),
              ),
            IgnorePointer(
              ignoring: !_visible,
              child: AnimatedOpacity(
                opacity: _visible ? 1 : 0,
                duration: const Duration(milliseconds: 200),
                child: DecoratedBox(
                  decoration: const BoxDecoration(color: Colors.black38),
                  child: Stack(
                    children: [
                      Center(child: _centerButtons(value)),
                      Positioned(
                        left: 0,
                        right: 0,
                        bottom: 0,
                        child: _seekBar(positionMs, durationMs, value),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _centerButtons(VideoPlayerValue value) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        _roundButton(Icons.replay_10_rounded, 30, 'Lùi 10 giây', () => _seekBy(-_skip)),
        const SizedBox(width: 28),
        _roundButton(
          _ended
              ? Icons.replay_rounded
              : value.isPlaying
                  ? Icons.pause_rounded
                  : Icons.play_arrow_rounded,
          44,
          value.isPlaying ? 'Tạm dừng' : 'Phát',
          _togglePlay,
        ),
        const SizedBox(width: 28),
        _roundButton(Icons.forward_10_rounded, 30, 'Tua 10 giây', () => _seekBy(_skip)),
      ],
    );
  }

  Widget _roundButton(IconData icon, double size, String tooltip, VoidCallback onTap) {
    return Material(
      color: Colors.black45,
      shape: const CircleBorder(),
      child: IconButton(
        tooltip: tooltip,
        iconSize: size,
        padding: EdgeInsets.all(size * 0.3),
        color: Colors.white,
        icon: Icon(icon),
        onPressed: onTap,
      ),
    );
  }

  Widget _seekBar(double positionMs, double durationMs, VideoPlayerValue value) {
    const timeStyle = TextStyle(
      color: Colors.white,
      fontSize: 12,
      fontFeatures: [FontFeature.tabularFigures()],
    );
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 0, 4, 4),
        child: Row(
          children: [
            Text(_format(Duration(milliseconds: positionMs.round())), style: timeStyle),
            Expanded(
              child: SliderTheme(
                data: SliderTheme.of(context).copyWith(
                  trackHeight: 3,
                  activeTrackColor: AppColors.brand,
                  inactiveTrackColor: Colors.white24,
                  thumbColor: AppColors.brand,
                  overlayShape: const RoundSliderOverlayShape(overlayRadius: 14),
                  thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 7),
                ),
                child: Slider(
                  value: positionMs,
                  max: durationMs > 0 ? durationMs : 1,
                  onChangeStart: (v) {
                    _hideTimer?.cancel();
                    setState(() => _dragMs = v);
                  },
                  onChanged: durationMs > 0 ? (v) => setState(() => _dragMs = v) : null,
                  onChangeEnd: (v) async {
                    await _c.seekTo(Duration(milliseconds: v.round()));
                    if (mounted) setState(() => _dragMs = null);
                    _scheduleHide();
                  },
                ),
              ),
            ),
            Text(_format(value.duration), style: timeStyle),
            IconButton(
              tooltip: value.isLooping ? 'Tắt lặp lại' : 'Lặp lại',
              color: value.isLooping ? AppColors.brand : Colors.white70,
              icon: const Icon(Icons.repeat_rounded, size: 20),
              onPressed: () {
                _c.setLooping(!value.isLooping);
                _show();
              },
            ),
          ],
        ),
      ),
    );
  }

  static String _format(Duration d) {
    final h = d.inHours;
    final m = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    return h > 0 ? '$h:$m:$s' : '$m:$s';
  }
}
