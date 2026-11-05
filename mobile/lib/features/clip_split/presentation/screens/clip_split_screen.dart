import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:video_player/video_player.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../../../core/widgets/video_controls.dart';
import '../../../profile/presentation/screens/billing_screen.dart';
import '../../data/clip_repository.dart';
import '../../domain/clip_models.dart';

/// "Cắt clip": a feature of its own. Pick a finished video, plan the clips (equal split,
/// by hand or AI suggestions), then every clip becomes a job in Tiến trình.
class ClipSplitScreen extends ConsumerStatefulWidget {
  const ClipSplitScreen({super.key, this.sourceJobId});

  /// Opens this video's plan right away (from a finished job's detail).
  final String? sourceJobId;

  @override
  ConsumerState<ClipSplitScreen> createState() => _ClipSplitScreenState();
}

class _ClipSplitScreenState extends ConsumerState<ClipSplitScreen> {
  late String? _sourceId = widget.sourceJobId;

  @override
  Widget build(BuildContext context) {
    final sourceId = _sourceId;
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: const StudioSubBar(title: 'Cắt clip', subtitle: 'Cắt video đã dịch thành nhiều clip'),
      body: sourceId == null
          ? _SourcePicker(onPick: (jobId) => setState(() => _sourceId = jobId))
          : _ClipEditor(
              key: ValueKey(sourceId),
              sourceId: sourceId,
              onChangeSource: () => setState(() => _sourceId = null),
            ),
    );
  }
}

// ---- Picking the finished video ---------------------------------------------------------

class _SourcePicker extends ConsumerStatefulWidget {
  const _SourcePicker({required this.onPick});

  final ValueChanged<String> onPick;

  @override
  ConsumerState<_SourcePicker> createState() => _SourcePickerState();
}

class _SourcePickerState extends ConsumerState<_SourcePicker> {
  final _search = TextEditingController();
  Timer? _debounce;
  List<ClipSourceSummary> _items = const [];
  int _total = 0;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _search.dispose();
    super.dispose();
  }

  Future<void> _load({bool more = false}) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final page = await ref
          .read(clipRepositoryProvider)
          .sources(search: _search.text, offset: more ? _items.length : 0);
      if (!mounted) return;
      setState(() {
        _items = more ? [..._items, ...page.items] : page.items;
        _total = page.total;
      });
    } catch (error) {
      if (mounted) setState(() => _error = errorMessage(error));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: EdgeInsets.fromLTRB(16, 14, 16, studioDockBottomPadding(context)),
      children: [
        const Text(
          'Chọn một video đã dịch hoặc lồng tiếng xong. Giọng và phụ đề giữ nguyên như bản đầy đủ; '
          'cắt clip không trừ phút của gói.',
          style: TextStyle(color: AppColors.muted, fontSize: 13, height: 1.4),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _search,
          decoration: const InputDecoration(prefixIcon: Icon(Icons.search_rounded), hintText: 'Tìm theo tên video'),
          onChanged: (_) {
            _debounce?.cancel();
            _debounce = Timer(const Duration(milliseconds: 350), _load);
          },
        ),
        const SizedBox(height: 12),
        if (_error != null)
          ErrorView(message: _error!, onRetry: _load)
        else if (!_loading && _items.isEmpty)
          const StudioEmptyState(
            icon: Icons.content_cut_rounded,
            title: 'Chưa có video nào đã xong',
            subtitle: 'Dịch hoặc lồng tiếng một video trước, rồi quay lại đây để cắt clip.',
          )
        else
          for (final item in _items)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: StudioCard(
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                onTap: () => widget.onPick(item.jobId),
                child: Row(
                  children: [
                    Container(
                      width: 38,
                      height: 38,
                      decoration: BoxDecoration(
                        color: AppColors.brandTint,
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(color: AppColors.brandBorder),
                      ),
                      child: const Icon(Icons.movie_rounded, color: AppColors.brand, size: 20),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(item.filename,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink, fontSize: 13.5)),
                          const SizedBox(height: 3),
                          Text(
                            [
                              if (item.duration != null) formatClipTime(item.duration!),
                              item.hasSubtitles ? 'có phụ đề' : 'không phụ đề',
                            ].join(' · '),
                            style: const TextStyle(fontSize: 12, color: AppColors.muted, fontWeight: FontWeight.w500),
                          ),
                        ],
                      ),
                    ),
                    const Icon(Icons.chevron_right_rounded, color: AppColors.mutedLight),
                  ],
                ),
              ),
            ),
        if (_loading)
          const Padding(
            padding: EdgeInsets.all(16),
            child: Center(child: CircularProgressIndicator(color: AppColors.brand)),
          )
        else if (_items.length < _total)
          TextButton(onPressed: () => _load(more: true), child: const Text('Xem thêm')),
      ],
    );
  }
}

// ---- The plan editor ----------------------------------------------------------------------

class _ClipEditor extends ConsumerStatefulWidget {
  const _ClipEditor({super.key, required this.sourceId, required this.onChangeSource});

  final String sourceId;
  final VoidCallback onChangeSource;

  @override
  ConsumerState<_ClipEditor> createState() => _ClipEditorState();
}

class _ClipEditorState extends ConsumerState<_ClipEditor> {
  ClipSource? _source;
  String? _error;
  VideoPlayerController? _video;
  double? _previewEnd;
  List<ClipDraft> _clips = [];
  ClipFrame _frame = ClipFrame.original;
  bool _snap = true;
  String? _busy;
  final _every = TextEditingController(text: '60');
  final _wish = TextEditingController();
  int _aiCount = 5;
  final _aiMin = TextEditingController(text: '15');
  final _aiMax = TextEditingController(text: '60');

  ClipRepository get _repo => ref.read(clipRepositoryProvider);

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _video?.removeListener(_onVideoTick);
    _video?.dispose();
    for (final controller in [_every, _wish, _aiMin, _aiMax]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final source = await _repo.source(widget.sourceId);
      if (!mounted) return;
      setState(() => _source = source);
      unawaited(_openVideo(source));
    } catch (error) {
      if (mounted) setState(() => _error = errorMessage(error));
    }
  }

  Future<void> _openVideo(ClipSource source) async {
    try {
      // video_player cannot refresh an expired token; do it through Dio first.
      final headers = await _repo.streamHeaders(source.videoUrl);
      final controller = VideoPlayerController.networkUrl(_repo.resolve(source.videoUrl), httpHeaders: headers);
      await controller.initialize();
      if (!mounted) return controller.dispose();
      controller.addListener(_onVideoTick);
      setState(() => _video = controller);
    } catch (error) {
      debugPrint('Clip source video failed: $error');
    }
  }

  void _onVideoTick() {
    final controller = _video;
    final end = _previewEnd;
    if (controller != null && end != null && controller.value.position.inMilliseconds >= end * 1000) {
      controller.pause();
      _previewEnd = null;
    }
    if (mounted) setState(() {});
  }

  double get _position => (_video?.value.position.inMilliseconds ?? 0) / 1000;

  void _seek(double seconds, {double? playUntil}) {
    final controller = _video;
    if (controller == null) return;
    _previewEnd = playUntil;
    controller.seekTo(Duration(milliseconds: (seconds * 1000).round()));
    if (playUntil != null) controller.play();
  }

  Future<void> _run(String busy, Future<void> Function() action) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _busy = busy);
    try {
      await action();
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  Future<bool> _confirmReplace() async {
    if (_clips.isEmpty) return true;
    return await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            content: const Text('Thay plan hiện tại?'),
            actions: [
              TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Không')),
              TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Thay')),
            ],
          ),
        ) ==
        true;
  }

  Future<void> _splitEqually() async {
    final seconds = double.tryParse(_every.text.replaceAll(',', '.'));
    if (seconds == null) return;
    if (!await _confirmReplace()) return;
    await _run('equal', () async {
      final clips = await _repo.planEqual(widget.sourceId, seconds, _frame);
      if (mounted) setState(() => _clips = clips);
    });
  }

  Future<void> _suggest() async {
    if (!await _confirmReplace()) return;
    await _run('ai', () async {
      final clips = await _repo.suggest(
        widget.sourceId,
        wish: _wish.text,
        count: _aiCount,
        minSeconds: double.tryParse(_aiMin.text) ?? 15,
        maxSeconds: double.tryParse(_aiMax.text) ?? 60,
        frame: _frame,
      );
      if (!mounted) return;
      setState(() => _clips = clips);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('AI gợi ý ${clips.length} đoạn — xem lại rồi bấm Tạo clip')),
      );
    });
  }

  void _addHere() {
    final source = _source!;
    final start = _position.clamp(0, (source.duration - source.limits.minClipSeconds).clamp(0, double.infinity)).toDouble();
    setState(() {
      _clips = [..._clips, ClipDraft(start: start, end: (start + 30).clamp(0, source.duration).toDouble(), frame: _frame)]
        ..sort((a, b) => a.start.compareTo(b.start));
    });
  }

  void _update(int index, ClipDraft clip) => setState(() => _clips = [..._clips]..[index] = clip);

  Future<void> _create() async {
    await _run('create', () async {
      await _repo.create(widget.sourceId, _clips, snap: _snap);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Đã tạo ${_clips.length} clip, đang cắt — theo dõi ở Tiến trình')),
      );
      context.go('/jobs');
    });
  }

  @override
  Widget build(BuildContext context) {
    // Watched before any early return so the editor rebuilds when the session arrives.
    final readOnly = ref.watch(sessionProvider).valueOrNull?.isReadOnly ?? true;
    final source = _source;
    if (_error != null) return ErrorView(message: _error!, onRetry: _load);
    if (source == null) return const Center(child: CircularProgressIndicator(color: AppColors.brand));
    final video = _video;
    final ready = planReady(_clips, source);
    final total = _clips.fold<double>(0, (sum, clip) => sum + clip.duration.clamp(0, double.infinity));

    return ListView(
      padding: EdgeInsets.fromLTRB(16, 14, 16, studioDockBottomPadding(context)),
      children: [
        Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(source.filename,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15, color: AppColors.ink)),
                  Text('Dài ${formatClipTime(source.duration)} · ${source.cues.length} câu phụ đề',
                      style: const TextStyle(fontSize: 12, color: AppColors.muted)),
                ],
              ),
            ),
            TextButton(onPressed: widget.onChangeSource, child: const Text('Đổi video')),
          ],
        ),
        const SizedBox(height: 10),
        ClipRRect(
          borderRadius: BorderRadius.circular(14),
          child: ColoredBox(
            color: const Color(0xFF0F172A),
            child: AspectRatio(
              aspectRatio: video != null && video.value.isInitialized ? video.value.aspectRatio : 16 / 9,
              child: video != null && video.value.isInitialized
                  ? ControlledVideo(controller: video)
                  : const Center(child: CircularProgressIndicator(color: AppColors.brand)),
            ),
          ),
        ),
        const SizedBox(height: 10),
        ClipTimeline(
          duration: source.duration,
          cues: source.cues,
          clips: _clips,
          position: _position,
          isValid: (clip) => clip.problem(source) == null,
          onSeek: (seconds) => _seek(seconds),
        ),
        const SizedBox(height: 14),
        StudioCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  const Text('Khung hình', style: TextStyle(fontWeight: FontWeight.w700)),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _FramePicker(
                      value: _frame,
                      canCrop: source.canCrop,
                      onChanged: (frame) => setState(() => _frame = frame),
                    ),
                  ),
                ],
              ),
              if (!source.canCrop)
                const Padding(
                  padding: EdgeInsets.only(top: 6),
                  child: Text('Video nguồn chưa in phụ đề đã bị xóa nên không dùng được kiểu crop giữa.',
                      style: TextStyle(fontSize: 12, color: AppColors.danger)),
                ),
              const SizedBox(height: 12),
              // Label on its own line: the field and button share the row at any text size.
              const Text('Chia đều — mỗi clip dài (giây)', style: TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(height: 6),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _every,
                      keyboardType: const TextInputType.numberWithOptions(decimal: true),
                      decoration: const InputDecoration(isDense: true, suffixText: 'giây'),
                    ),
                  ),
                  const SizedBox(width: 10),
                  OutlinedButton(
                    onPressed: readOnly || _busy != null ? null : _splitEqually,
                    child: _busy == 'equal' ? const _Spinner() : const Text('Chia đều'),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                onPressed: readOnly || video == null ? null : _addHere,
                icon: const Icon(Icons.add_rounded, size: 18),
                label: const Text('Thêm đoạn từ vị trí đang xem'),
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                value: _snap,
                onChanged: (value) => setState(() => _snap = value),
                title: const Text('Khớp ranh giới câu'),
                subtitle: const Text('Điểm cắt tự rơi vào khoảng lặng giữa hai câu'),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        _AiCard(
          source: source,
          wish: _wish,
          count: _aiCount,
          minSeconds: _aiMin,
          maxSeconds: _aiMax,
          busy: _busy == 'ai',
          onCount: (value) => setState(() => _aiCount = value),
          onSuggest: readOnly || _busy != null || !source.aiAvailable ? null : _suggest,
        ),
        const SizedBox(height: 14),
        if (_clips.isEmpty)
          const Text('Chưa có đoạn nào. Chia đều, nhờ AI gợi ý hoặc thêm đoạn từ vị trí đang xem.',
              style: TextStyle(color: AppColors.muted, fontSize: 13))
        else
          for (final (index, clip) in _clips.indexed)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: _ClipCard(
                index: index,
                clip: clip,
                source: source,
                position: _position,
                onChanged: (updated) => _update(index, updated),
                onRemove: () => setState(() => _clips = [..._clips]..removeAt(index)),
                onPreview: () => _seek(clip.start, playUntil: clip.end),
              ),
            ),
        const SizedBox(height: 8),
        if (_clips.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text('${_clips.length} clip · tổng ${formatClipTime(total)}',
                textAlign: TextAlign.center, style: const TextStyle(color: AppColors.muted)),
          ),
        StudioGradientButton(
          onPressed: readOnly || !ready || _busy != null ? null : _create,
          isLoading: _busy == 'create',
          icon: Icons.content_cut_rounded,
          label: _clips.isEmpty ? 'Tạo clip' : 'Tạo ${_clips.length} clip',
        ),
      ],
    );
  }
}

class _Spinner extends StatelessWidget {
  const _Spinner();

  @override
  Widget build(BuildContext context) =>
      const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.brand));
}

class _FramePicker extends StatelessWidget {
  const _FramePicker({required this.value, required this.canCrop, required this.onChanged});

  final ClipFrame value;
  final bool canCrop;
  final ValueChanged<ClipFrame> onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        color: AppColors.surfaceSoft,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.line),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<ClipFrame>(
          value: value,
          isExpanded: true,
          isDense: true,
          onChanged: (frame) => frame == null ? null : onChanged(frame),
          items: [
            for (final frame in ClipFrame.values)
              DropdownMenuItem(
                value: frame,
                enabled: frame != ClipFrame.verticalCrop || canCrop,
                child: Text(
                  frame.label,
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                    color: frame == ClipFrame.verticalCrop && !canCrop ? AppColors.mutedLight : AppColors.ink,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _AiCard extends StatelessWidget {
  const _AiCard({
    required this.source,
    required this.wish,
    required this.count,
    required this.minSeconds,
    required this.maxSeconds,
    required this.busy,
    required this.onCount,
    required this.onSuggest,
  });

  final ClipSource source;
  final TextEditingController wish;
  final int count;
  final TextEditingController minSeconds;
  final TextEditingController maxSeconds;
  final bool busy;
  final ValueChanged<int> onCount;
  final VoidCallback? onSuggest;

  @override
  Widget build(BuildContext context) {
    final available = source.aiAvailable;
    Widget secondsField(TextEditingController controller) => SizedBox(
          width: 56,
          child: TextField(
            controller: controller,
            enabled: available,
            keyboardType: TextInputType.number,
            textAlign: TextAlign.center,
            style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13),
            decoration: const InputDecoration(isDense: true, filled: true, fillColor: Colors.white),
          ),
        );
    return StudioCard(
      topAccentColor: AppColors.brand,
      backgroundColor: AppColors.brandTint,
      borderColor: AppColors.brandBorder,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text(
            '✨ AI gợi ý đoạn hay',
            style: TextStyle(fontWeight: FontWeight.w800, fontSize: 14.5, color: AppColors.brandDark),
          ),
          const SizedBox(height: 4),
          Text(
            available ? 'AI đọc phụ đề rồi đề xuất, bạn sửa tiếp trước khi tạo clip.' : (source.aiReason ?? ''),
            style: TextStyle(fontSize: 12, color: available ? AppColors.muted : AppColors.danger, height: 1.35),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: wish,
            enabled: available,
            maxLength: 300,
            maxLines: 2,
            minLines: 1,
            decoration: const InputDecoration(
              filled: true,
              fillColor: Colors.white,
              hintText: 'Bạn muốn cắt đoạn nào? Ví dụ: các đoạn đánh nhau, đoạn hài hước',
              counterText: '',
            ),
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 10,
            runSpacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              const Text('Số clip', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 13)),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: AppColors.line),
                ),
                child: DropdownButtonHideUnderline(
                  child: DropdownButton<int>(
                    value: count,
                    isDense: true,
                    onChanged: available ? (value) => value == null ? null : onCount(value) : null,
                    items: [
                      for (final value in const [3, 5, 8, 10])
                        DropdownMenuItem(
                          value: value,
                          child: Text('$value', style: const TextStyle(fontWeight: FontWeight.w700)),
                        ),
                    ],
                  ),
                ),
              ),
              const Text('Mỗi clip', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 13)),
              secondsField(minSeconds),
              const Text('–', style: TextStyle(fontWeight: FontWeight.w700)),
              secondsField(maxSeconds),
              const Text('giây', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
            ],
          ),
          const SizedBox(height: 12),
          StudioGradientButton(
            onPressed: onSuggest,
            isLoading: busy,
            icon: Icons.auto_awesome_rounded,
            label: busy ? 'AI đang đọc phụ đề…' : 'Gợi ý',
          ),
          if (!available && (source.aiReason ?? '').toLowerCase().contains('nâng cấp'))
            TextButton(
              onPressed: () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => const BillingScreen())),
              child: const Text('Xem gói'),
            ),
        ],
      ),
    );
  }
}

class _ClipCard extends StatelessWidget {
  const _ClipCard({
    required this.index,
    required this.clip,
    required this.source,
    required this.position,
    required this.onChanged,
    required this.onRemove,
    required this.onPreview,
  });

  final int index;
  final ClipDraft clip;
  final ClipSource source;
  final double position;
  final ValueChanged<ClipDraft> onChanged;
  final VoidCallback onRemove;
  final VoidCallback onPreview;

  @override
  Widget build(BuildContext context) {
    final problem = clip.problem(source);
    return StudioCard(
      padding: const EdgeInsets.fromLTRB(12, 10, 8, 10),
      backgroundColor: problem == null ? AppColors.surface : const Color(0xFFFEF2F2),
      borderColor: problem == null ? AppColors.line : const Color(0xFFFCA5A5),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Container(
                width: 26,
                height: 26,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: AppColors.brand,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  '${index + 1}',
                  style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w800),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: TextFormField(
                  key: ValueKey('title-$index-${clip.start}'),
                  initialValue: clip.title,
                  maxLength: 120,
                  style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5),
                  decoration: InputDecoration(isDense: true, hintText: 'Phần ${index + 1}', counterText: ''),
                  onChanged: (title) => onChanged(clip.copyWith(title: title)),
                ),
              ),
              IconButton(
                onPressed: onPreview,
                icon: const Icon(Icons.play_arrow_rounded, color: AppColors.brand),
                tooltip: 'Xem đoạn này',
              ),
              IconButton(
                onPressed: onRemove,
                icon: const Icon(Icons.close_rounded, color: AppColors.muted),
                tooltip: 'Xóa đoạn',
              ),
            ],
          ),
          const SizedBox(height: 6),
          _TimeRow(
            label: 'Từ',
            value: clip.start,
            onChanged: (seconds) => onChanged(clip.copyWith(start: seconds.clamp(0, source.duration).toDouble())),
            onHere: () => onChanged(clip.copyWith(start: position)),
          ),
          _TimeRow(
            label: 'Đến',
            value: clip.end,
            onChanged: (seconds) => onChanged(clip.copyWith(end: seconds.clamp(0, source.duration).toDouble())),
            onHere: () => onChanged(clip.copyWith(end: position)),
          ),
          Row(
            children: [
              Expanded(
                child: _FramePicker(
                  value: clip.frame,
                  canCrop: source.canCrop,
                  onChanged: (frame) => onChanged(clip.copyWith(frame: frame)),
                ),
              ),
              const SizedBox(width: 10),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: problem == null ? AppColors.surfaceSoft : const Color(0xFFFEE2E2),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: problem == null ? AppColors.line : const Color(0xFFFECACA)),
                ),
                child: Text(
                  problem ?? formatClipTime(clip.duration),
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    color: problem == null ? AppColors.inkSecondary : AppColors.danger,
                  ),
                ),
              ),
              const SizedBox(width: 6),
            ],
          ),
        ],
      ),
    );
  }
}

/// A time field that keeps what is being typed and reports only readable times.
class _TimeRow extends StatefulWidget {
  const _TimeRow({required this.label, required this.value, required this.onChanged, required this.onHere});

  final String label;
  final double value;
  final ValueChanged<double> onChanged;
  final VoidCallback onHere;

  @override
  State<_TimeRow> createState() => _TimeRowState();
}

class _TimeRowState extends State<_TimeRow> {
  late final _controller = TextEditingController(text: formatClipTime(widget.value));
  final _focus = FocusNode();

  @override
  void initState() {
    super.initState();
    _focus.addListener(() {
      if (!_focus.hasFocus) _commit();
    });
  }

  @override
  void didUpdateWidget(_TimeRow old) {
    super.didUpdateWidget(old);
    if (!_focus.hasFocus && old.value != widget.value) _controller.text = formatClipTime(widget.value);
  }

  @override
  void dispose() {
    _controller.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _commit() {
    final seconds = parseClipTime(_controller.text);
    if (seconds == null) {
      _controller.text = formatClipTime(widget.value);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Thời điểm phải có dạng phút:giây, ví dụ 1:23.5')),
      );
    } else if (seconds != widget.value) {
      widget.onChanged(seconds);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        children: [
          SizedBox(width: 34, child: Text(widget.label, style: const TextStyle(color: AppColors.muted))),
          Expanded(
            child: TextField(
              controller: _controller,
              focusNode: _focus,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              decoration: const InputDecoration(isDense: true),
              onSubmitted: (_) => _commit(),
            ),
          ),
          IconButton(
            onPressed: widget.onHere,
            icon: const Icon(Icons.my_location_rounded, size: 20),
            tooltip: 'Lấy vị trí đang xem',
          ),
        ],
      ),
    );
  }
}

/// The whole video as a bar: grey ticks are subtitle cues, orange blocks are clips
/// (red when invalid), the dark line is the playhead. Tapping seeks.
class ClipTimeline extends StatelessWidget {
  const ClipTimeline({
    super.key,
    required this.duration,
    required this.cues,
    required this.clips,
    required this.position,
    required this.isValid,
    required this.onSeek,
  });

  final double duration;
  final List<ClipCue> cues;
  final List<ClipDraft> clips;
  final double position;
  final bool Function(ClipDraft) isValid;
  final ValueChanged<double> onSeek;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) => GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapDown: (details) =>
            onSeek((details.localPosition.dx / constraints.maxWidth).clamp(0.0, 1.0) * duration),
        child: CustomPaint(
          size: Size(constraints.maxWidth, 30),
          painter: _TimelinePainter(duration, cues, clips, position, isValid),
        ),
      ),
    );
  }
}

class _TimelinePainter extends CustomPainter {
  _TimelinePainter(this.duration, this.cues, this.clips, this.position, this.isValid);

  final double duration;
  final List<ClipCue> cues;
  final List<ClipDraft> clips;
  final double position;
  final bool Function(ClipDraft) isValid;

  @override
  void paint(Canvas canvas, Size size) {
    final total = duration <= 0 ? 1.0 : duration;
    double x(double seconds) => (seconds / total).clamp(0.0, 1.0) * size.width;
    canvas.drawRRect(RRect.fromRectAndRadius(Offset.zero & size, const Radius.circular(8)),
        Paint()..color = AppColors.surfaceMuted);
    final cuePaint = Paint()..color = AppColors.lineStrong;
    for (final cue in cues) {
      canvas.drawRRect(
        RRect.fromLTRBR(x(cue.start), 4, x(cue.end).clamp(x(cue.start) + 1, size.width), 9, const Radius.circular(2)),
        cuePaint,
      );
    }
    for (final (index, clip) in clips.indexed) {
      final rect = RRect.fromLTRBR(x(clip.start), 12, x(clip.end), size.height - 3, const Radius.circular(5));
      canvas.drawRRect(rect, Paint()..color = (isValid(clip) ? AppColors.brand : AppColors.danger).withValues(alpha: 0.8));
      final label = TextPainter(
        text: TextSpan(
          text: '${index + 1}',
          style: const TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.w800),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      if (label.width + 2 < rect.width) {
        label.paint(canvas, Offset(rect.center.dx - label.width / 2, rect.center.dy - label.height / 2));
      }
    }
    canvas.drawRect(Rect.fromLTWH(x(position) - 1, 0, 2, size.height), Paint()..color = AppColors.ink);
  }

  @override
  bool shouldRepaint(_TimelinePainter old) =>
      old.position != position || old.clips != clips || old.cues != cues || old.duration != duration;
}
