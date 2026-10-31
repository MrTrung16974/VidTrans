import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:path_provider/path_provider.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../../home/presentation/screens/home_shell.dart';
import '../../../logo_branding/presentation/providers/branding_providers.dart';
import '../../data/translation_repository.dart';
import '../../domain/options.dart';
import '../../domain/providers.dart';
import '../../domain/translation_config.dart';
import '../providers/translate_form_controller.dart';
import '../widgets/form_fields.dart';
import '../widgets/voice_preview_button.dart';

class TranslateScreen extends ConsumerStatefulWidget {
  const TranslateScreen({super.key});

  @override
  ConsumerState<TranslateScreen> createState() => _TranslateScreenState();
}

class _TranslateScreenState extends ConsumerState<TranslateScreen> {
  late final TextEditingController _links;
  late final TextEditingController _batchName;
  int _activeStep = 1;

  final _step1Key = GlobalKey();
  final _step2Key = GlobalKey();
  final _step3Key = GlobalKey();
  final _step4Key = GlobalKey();

  TranslateFormController get _form => ref.read(translateFormProvider.notifier);

  @override
  void initState() {
    super.initState();
    final state = ref.read(translateFormProvider);
    _links = TextEditingController(text: state.linksText);
    _batchName = TextEditingController(text: state.config.batchName);
  }

  @override
  void dispose() {
    _links.dispose();
    _batchName.dispose();
    super.dispose();
  }

  void _update(TranslationConfig Function(TranslationConfig) change) =>
      _form.updateConfig(change(ref.read(translateFormProvider).config));

  Future<void> _pickVideos() async {
    final files = await FilePicker.pickFiles(type: FileType.video);
    _form.addFiles([for (final file in files) ?await _toLocal(file)]);
  }

  Future<void> _pickMusic() async {
    final file = await FilePicker.pickFile(type: FileType.audio);
    if (file == null) return;
    final media = await _toLocal(file);
    if (media != null) _form.setMusic(media);
  }

  /// Uploads stream from a local path. Pickers that hand back a non-file URI
  /// (cloud providers) are copied into the temp folder first.
  Future<PickedMedia?> _toLocal(PlatformFile file) async {
    final path = file.path;
    if (path != null) {
      return PickedMedia(path: path, name: file.name, size: file.lengthSync());
    }
    try {
      final target =
          '${(await getTemporaryDirectory()).path}/${DateTime.now().microsecondsSinceEpoch}_${file.name}';
      await file.xFile.saveTo(target);
      return PickedMedia(
        path: target,
        name: file.name,
        size: await file.length(),
      );
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Không đọc được file ${file.name}')),
        );
      }
      return null;
    }
  }

  Future<void> _submit() async {
    FocusScope.of(context).unfocus();
    _update((c) => c.copyWith(batchName: _batchName.text));
    final batch = await _form.submit();
    if (batch == null || !mounted) return;
    _links.clear();
    _batchName.text = ref.read(translateFormProvider).config.batchName;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(
        'Đã tạo ${batch.jobIds.length} công việc trong ${batch.name}',
      ),
    ));
    context.go(
      batch.jobIds.length == 1 ? '/jobs/${batch.jobIds.single}' : '/jobs',
    );
  }

  /// Moves remembered choices that the server no longer allows to a usable
  /// provider/voice, as the web form does after loading provider status.
  void _reconcile(
    TranslationConfig config,
    List<TranslationProviderInfo>? translation,
    List<TtsProviderInfo>? tts,
  ) {
    var next = config;
    if (translation != null && translation.isNotEmpty) {
      next = next.copyWith(
        translationProvider:
            pickTranslationProvider(next.translationProvider, translation),
      );
    }
    if (tts != null && tts.isNotEmpty) {
      final providerKey = pickTtsProvider(next.ttsProvider, tts);
      final provider = tts.where((p) => p.key == providerKey).firstOrNull;
      final voices = provider?.voiceChoices ?? const <TtsVoice>[];
      final voice =
          voices.where((v) => v.key == next.voiceKey).firstOrNull ??
              voices.firstOrNull;
      next = next.copyWith(
        ttsProvider: providerKey,
        voiceKey: voice?.key ?? next.voiceKey,
        voiceGender: voice?.gender ?? next.voiceGender,
      );
    }
    if (next.translationProvider != config.translationProvider ||
        next.ttsProvider != config.ttsProvider ||
        next.voiceKey != config.voiceKey ||
        next.voiceGender != config.voiceGender) {
      WidgetsBinding.instance
          .addPostFrameCallback((_) => _form.updateConfig(next));
    }
  }

  void _jumpToStep(int step, GlobalKey key) {
    setState(() => _activeStep = step);
    final ctx = key.currentContext;
    if (ctx != null) {
      Scrollable.ensureVisible(
        ctx,
        duration: const Duration(milliseconds: 320),
        curve: Curves.easeOutCubic,
        alignment: 0.04,
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final readOnly = ref.watch(sessionProvider).valueOrNull?.isReadOnly ?? true;
    if (readOnly) {
      return const Scaffold(
        backgroundColor: AppColors.bg,
        appBar: StudioTopBar(),
        body: StudioEmptyState(
          icon: Icons.lock_outline_rounded,
          title: 'Tài khoản chỉ xem',
          subtitle: 'Tài khoản chỉ xem không thể tạo công việc mới.',
        ),
      );
    }

    final state = ref.watch(translateFormProvider);
    final config = state.config;
    final translation = ref.watch(translationProvidersProvider).valueOrNull;
    final tts = ref.watch(ttsProvidersProvider).valueOrNull;
    _reconcile(config, translation, tts);

    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: const StudioTopBar(),
      body: AbsorbPointer(
        absorbing: state.busy,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(14, 14, 14, 28),
          children: [
            _heroHeader(),
            const SizedBox(height: 14),
            _workflowStepsNav(),
            const SizedBox(height: 14),
            KeyedSubtree(
              key: _step1Key,
              child: _sourcesSection(state),
            ),
            KeyedSubtree(
              key: _step2Key,
              child: FormSection(
                stepNumber: 2,
                title: 'Cấu hình xử lý',
                subtitle: 'Đặt tên lô và chọn chế độ âm thanh đầu ra',
                children: [
                  TextField(
                    controller: _batchName,
                    maxLength: 120,
                    decoration: const InputDecoration(
                      hintText: 'Ví dụ: Review phim tập 1-10',
                      labelText: 'Tên batch (tùy chọn)',
                      counterText: '',
                      prefixIcon: Icon(Icons.folder_outlined, size: 19),
                    ),
                    onChanged: (value) =>
                        _update((c) => c.copyWith(batchName: value)),
                  ),
                  _modeBody(state),
                ],
              ),
            ),
            // Download-only skips every translation step; the logo still applies.
            if (!config.downloadOnly) ...[
              _languageSection(config, translation),
              _recognitionSection(config),
              if (config.hasVoice) _voiceSection(config, tts),
              _audioSection(config),
            ],
            _logoSection(config),
            if (!config.downloadOnly) ...[
              KeyedSubtree(
                key: _step3Key,
                child: _subtitleStyleSection(config),
              ),
              KeyedSubtree(
                key: _step4Key,
                child: _tiktokSection(config),
              ),
              _ocrSection(config),
            ],
            const SizedBox(height: 4),
            _submitCard(state),
          ],
        ),
      ),
    );
  }

  Widget _heroHeader() {
    return StudioPageHeader(
      eyebrow: 'Studio tự động hóa',
      eyebrowIcon: Icons.bolt_rounded,
      eyebrowNote: 'Tối đa 50 video / batch',
      title: 'Cấu hình một lần, xử lý cả batch',
      subtitle:
          'Thêm video từ thiết bị hoặc link Douyin/TikTok/Facebook/Youtube, chọn ngôn ngữ và phụ đề để hệ thống tự động xử lý dưới nền.',
      actions: [
        const StudioSystemBadge(label: 'Hàng đợi sẵn sàng'),
        InkWell(
          onTap: () => context.go('/jobs'),
          borderRadius: BorderRadius.circular(999),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 5),
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(999),
              border: Border.all(color: AppColors.lineStrong),
            ),
            child: const Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  'Xem tiến trình',
                  style: TextStyle(
                    fontSize: 11.5,
                    fontWeight: FontWeight.w700,
                    color: AppColors.ink,
                  ),
                ),
                SizedBox(width: 4),
                Icon(
                  Icons.arrow_forward_rounded,
                  size: 13,
                  color: AppColors.brand,
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _workflowStepsNav() {
    final downloadOnly = ref.watch(translateFormProvider).config.downloadOnly;
    final allSteps = <({int step, String label, String sub, GlobalKey key})>[
      (step: 1, label: 'Nguồn video', sub: 'File hoặc link', key: _step1Key),
      (
        step: 2,
        label: 'Cấu hình xử lý',
        sub: 'Chế độ & giọng',
        key: _step2Key,
      ),
      (
        step: 3,
        label: 'Vị trí phụ đề',
        sub: 'Cỡ chữ & xem trước',
        key: _step3Key,
      ),
      (
        step: 4,
        label: 'Nội dung đăng',
        sub: 'Caption & hashtag',
        key: _step4Key,
      ),
    ];
    // Subtitle and caption steps are hidden in download-only mode.
    final steps = downloadOnly ? allSteps.where((s) => s.step <= 2).toList() : allSteps;

    return Container(
      padding: const EdgeInsets.all(7),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.92),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.line),
        boxShadow: AppColors.cardShadow,
      ),
      child: Column(
        children: [
          Row(
            children: [
              Expanded(child: _stepNavItem(steps[0])),
              const SizedBox(width: 6),
              Expanded(child: _stepNavItem(steps[1])),
            ],
          ),
          const SizedBox(height: 6),
          Row(
            children: [
              Expanded(child: _stepNavItem(steps[2])),
              const SizedBox(width: 6),
              Expanded(child: _stepNavItem(steps[3])),
            ],
          ),
        ],
      ),
    );
  }

  Widget _stepNavItem(
      ({int step, String label, String sub, GlobalKey key}) item) {
    final active = _activeStep == item.step;
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: () => _jumpToStep(item.step, item.key),
        borderRadius: BorderRadius.circular(13),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 8),
          decoration: BoxDecoration(
            color: active ? AppColors.brandTint : Colors.transparent,
            borderRadius: BorderRadius.circular(13),
            border: Border.all(
              color: active ? AppColors.brandBorder : Colors.transparent,
            ),
          ),
          child: Row(
            children: [
              Container(
                width: 24,
                height: 24,
                decoration: BoxDecoration(
                  gradient: active ? AppColors.brandGradient : null,
                  color: active ? null : const Color(0xFFF1F5F9),
                  borderRadius: BorderRadius.circular(7),
                ),
                alignment: Alignment.center,
                child: Text(
                  '${item.step}',
                  style: TextStyle(
                    fontSize: 11.5,
                    fontWeight: FontWeight.w800,
                    color: active ? Colors.white : AppColors.inkSoft,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      item.label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 12.2,
                        fontWeight: active ? FontWeight.w800 : FontWeight.w700,
                        color: active ? AppColors.brandDark : AppColors.ink,
                        letterSpacing: -0.15,
                      ),
                    ),
                    Text(
                      item.sub,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 10.5,
                        color: AppColors.muted,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _sourcesSection(TranslateFormState state) {
    final links = state.links;
    return FormSection(
      stepNumber: 1,
      title: 'Thêm video nguồn',
      subtitle:
          'Tải file từ thiết bị hoặc dán liên kết TikTok, Douyin, YouTube hoặc Facebook — tối đa 50 video mỗi batch.',
      trailing: StudioEyebrow(
        label: '${state.sourceCount}/50 nguồn',
      ),
      children: [
        StudioDashedBox(
          onTap: _pickVideos,
          padding: const EdgeInsets.fromLTRB(14, 14, 14, 16),
          backgroundColor: const Color(0xFFFCFDFE),
          borderColor: const Color(0xFFCBD5E1),
          borderRadius: 16,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  const Text(
                    '1A · TỪ THIẾT BỊ',
                    style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w800,
                      letterSpacing: 0.55,
                      color: AppColors.brandDark,
                    ),
                  ),
                  if (state.files.isNotEmpty)
                    Text(
                      '${state.files.length} file đã chọn',
                      style: const TextStyle(
                        fontSize: 11.5,
                        fontWeight: FontWeight.w700,
                        color: AppColors.teal,
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Container(
                    width: 44,
                    height: 44,
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(13),
                      border: Border.all(color: const Color(0xFFFFEDD5)),
                      boxShadow: const [
                        BoxShadow(
                          color: Color(0x1AEA580C),
                          blurRadius: 12,
                          offset: Offset(0, 4),
                        ),
                      ],
                    ),
                    alignment: Alignment.center,
                    child: const Icon(
                      Icons.file_upload_outlined,
                      size: 22,
                      color: AppColors.brand,
                    ),
                  ),
                  const SizedBox(width: 12),
                  const Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Chạm để chọn video từ máy',
                          style: TextStyle(
                            fontSize: 14.5,
                            fontWeight: FontWeight.w800,
                            color: AppColors.ink,
                            letterSpacing: -0.2,
                          ),
                        ),
                        SizedBox(height: 2),
                        Text(
                          'Hỗ trợ MP4, MOV, MKV · Tối đa 50 video',
                          style: TextStyle(
                            fontSize: 12,
                            color: AppColors.muted,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
        for (final file in state.files)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            decoration: BoxDecoration(
              color: AppColors.surfaceSubtle,
              borderRadius: BorderRadius.circular(11),
              border: Border.all(color: AppColors.line),
            ),
            child: Row(
              children: [
                const Icon(
                  Icons.movie_outlined,
                  size: 19,
                  color: AppColors.brand,
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        file.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w700,
                          color: AppColors.ink,
                        ),
                      ),
                      if (file.size != null)
                        Text(
                          _formatSize(file.size!),
                          style: const TextStyle(
                            fontSize: 11.5,
                            color: AppColors.muted,
                          ),
                        ),
                    ],
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.close_rounded, size: 18),
                  visualDensity: VisualDensity.compact,
                  onPressed: () => _form.removeFile(file),
                ),
              ],
            ),
          ),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: AppColors.surfaceSubtle,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: AppColors.line),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Text(
                    '1B · TỪ LIÊN KẾT MẠNG XÃ HỘI',
                    style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w800,
                      letterSpacing: 0.5,
                      color: AppColors.teal,
                    ),
                  ),
                  const Spacer(),
                  if (links.isNotEmpty)
                    StudioStatusPill(
                      status: 'ready',
                      label: '${links.length} link',
                      compact: true,
                    ),
                ],
              ),
              const SizedBox(height: 8),
              TextField(
                controller: _links,
                minLines: 2,
                maxLines: 5,
                maxLength: 12000,
                keyboardType: TextInputType.multiline,
                decoration: const InputDecoration(
                  hintText:
                      'Dán link TikTok, Douyin, YouTube hoặc Facebook — mỗi link một dòng',
                  counterText: '',
                  prefixIcon: Icon(Icons.link_rounded, color: AppColors.teal),
                ),
                onChanged: _form.setLinks,
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _modeBody(TranslateFormState state) {
    final config = state.config;
    const modes = <({
      int value,
      String title,
      String badge,
      String desc,
      IconData icon,
    })>[
      (
        value: 1,
        title: 'Chỉ phụ đề',
        badge: 'Tốc độ cao',
        desc: 'Giữ nguyên âm thanh gốc, tạo và gắn phụ đề dịch vào video.',
        icon: Icons.subtitles_outlined,
      ),
      (
        value: 2,
        title: 'Phụ đề + Thuyết minh',
        badge: 'Phổ biến',
        desc: 'Tạo phụ đề kết hợp giọng đọc AI tự nhiên theo ngữ cảnh.',
        icon: Icons.record_voice_over_outlined,
      ),
      (
        value: 3,
        title: 'Phụ đề + Thuyết minh + Nhạc',
        badge: 'Studio Pro',
        desc: 'Kết hợp phụ đề, giọng thuyết minh AI và nhạc nền tùy chọn.',
        icon: Icons.library_music_outlined,
      ),
      (
        value: 4,
        title: 'Chỉ tải video',
        badge: 'Không dịch',
        desc: 'Tải video gốc từ link hoặc file về, không nhận diện, dịch hay lồng tiếng.',
        icon: Icons.download_rounded,
      ),
    ];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const Text(
          'Chế độ xử lý đầu ra',
          style: TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w700,
            color: AppColors.inkSoft,
          ),
        ),
        const SizedBox(height: 8),
        for (var i = 0; i < modes.length; i++) ...[
          if (i > 0) const SizedBox(height: 8),
          _ModeChoiceCard(
            title: modes[i].title,
            badge: modes[i].badge,
            description: modes[i].desc,
            icon: modes[i].icon,
            selected: config.mode == modes[i].value,
            onTap: () => _update((c) => c.copyWith(mode: modes[i].value)),
          ),
        ],
        if (config.needsMusic) ...[
          const SizedBox(height: 10),
          OutlinedButton.icon(
            onPressed: _pickMusic,
            icon: const Icon(Icons.music_note_outlined),
            label: Text(
              state.music?.name ?? 'Chọn nhạc nền dùng chung (MP3/WAV/M4A)',
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ],
    );
  }

  Widget _languageSection(
    TranslationConfig config,
    List<TranslationProviderInfo>? providers,
  ) {
    final byKey = {
      for (final p in providers ?? const <TranslationProviderInfo>[]) p.key: p,
    };
    final options = [
      for (final key in translationProviderOrder)
        (value: key, label: byKey[key]?.label ?? key),
    ];
    return FormSection(
      title: 'Ngôn ngữ & dịch thuật',
      subtitle: 'Chọn cặp ngôn ngữ chuyển đổi và mô hình AI dịch thuật',
      icon: Icons.translate_rounded,
      children: [
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: AppColors.surfaceSubtle,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: AppColors.line),
          ),
          child: Column(
            children: [
              SelectField(
                label: 'Ngôn ngữ nguồn (trong video)',
                value: config.sourceLanguage,
                options: sourceLanguageOptions,
                onChanged: (v) => _update((c) => c.copyWith(sourceLanguage: v)),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Row(
                  children: [
                    const Expanded(child: Divider()),
                    Container(
                      margin: const EdgeInsets.symmetric(horizontal: 10),
                      padding: const EdgeInsets.symmetric(
                        horizontal: 10,
                        vertical: 4,
                      ),
                      decoration: BoxDecoration(
                        color: AppColors.brandSoft,
                        borderRadius: BorderRadius.circular(999),
                        border: Border.all(color: AppColors.brandBorder),
                      ),
                      child: const Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            Icons.south_rounded,
                            size: 13,
                            color: AppColors.brandDark,
                          ),
                          SizedBox(width: 4),
                          Text(
                            'CHUYỂN ĐỔI SANG',
                            style: TextStyle(
                              fontSize: 9.5,
                              fontWeight: FontWeight.w800,
                              letterSpacing: 0.5,
                              color: AppColors.brandDark,
                            ),
                          ),
                        ],
                      ),
                    ),
                    const Expanded(child: Divider()),
                  ],
                ),
              ),
              SelectField(
                label: 'Ngôn ngữ đầu ra',
                value: config.targetLanguage,
                options: targetLanguageOptions,
                onChanged: (v) => _update((c) => c.copyWith(targetLanguage: v)),
              ),
            ],
          ),
        ),
        SelectField(
          label: 'Bộ dịch AI',
          value: config.translationProvider,
          options: options,
          disabled: {
            if (providers != null)
              for (final key in translationProviderOrder)
                if (!(byKey[key]?.usable ?? false))
                  key: byKey[key]?.statusNote ?? 'không khả dụng',
          },
          helper: byKey[config.translationProvider]?.description,
          onChanged: (v) => _update((c) => c.copyWith(translationProvider: v)),
        ),
      ],
    );
  }

  Widget _recognitionSection(TranslationConfig config) {
    return FormSection(
      title: 'Nhận diện lời thoại',
      subtitle: 'Tách chữ bằng giọng nói (Whisper) hoặc quét phụ đề gốc (OCR)',
      icon: Icons.closed_caption_outlined,
      children: [
        SelectField(
          label: 'Nguồn phụ đề',
          value: config.ocrAllowed ? config.subtitleSource : 'speech',
          options: subtitleSourceOptions,
          disabled: {
            if (!config.ocrAllowed)
              for (final o in subtitleSourceOptions)
                if (o.value != 'speech')
                  o.value: 'chỉ dùng cho video tiếng Trung',
          },
          onChanged: (v) => _update((c) => c.copyWith(subtitleSource: v)),
        ),
        SelectField(
          label: 'Mô hình Whisper',
          value: config.whisperModel,
          options: whisperModelOptions,
          onChanged: (v) => _update((c) => c.copyWith(whisperModel: v)),
        ),
      ],
    );
  }

  Widget _voiceSection(
    TranslationConfig config,
    List<TtsProviderInfo>? providers,
  ) {
    final byKey = {
      for (final p in providers ?? const <TtsProviderInfo>[]) p.key: p,
    };
    final selected = byKey[config.ttsProvider];
    final voices = selected?.voiceChoices ?? const <TtsVoice>[];
    return FormSection(
      title: 'Giọng đọc AI (TTS)',
      subtitle: 'Chọn nhà cung cấp giọng đọc, nhân vật và tốc độ lồng tiếng',
      icon: Icons.record_voice_over_outlined,
      children: [
        SelectField(
          label: 'Nhà cung cấp giọng đọc',
          value: config.ttsProvider,
          options: [
            for (final key in ttsProviderOrder)
              (value: key, label: byKey[key]?.label ?? key),
          ],
          disabled: {
            if (providers != null)
              for (final key in ttsProviderOrder)
                if (!(byKey[key]?.usable ?? false))
                  key: byKey[key]?.statusNote ?? 'không khả dụng',
          },
          helper: selected?.freeNote.isNotEmpty ?? false
              ? selected!.freeNote
              : 'Khóa API chỉ được lưu ở máy chủ.',
          onChanged: (v) {
            final voices = byKey[v]?.voiceChoices ?? const <TtsVoice>[];
            final voice =
                voices.where((x) => x.key == config.voiceKey).firstOrNull ??
                    voices.firstOrNull;
            _update((c) => c.copyWith(
                  ttsProvider: v,
                  voiceKey: voice?.key ?? 'female',
                  voiceGender: voice?.gender ?? 'female',
                ));
          },
        ),
        SelectField(
          label: 'Chế độ chọn giọng',
          value: config.voiceMode,
          options: [
            (
              value: 'auto',
              label: selected?.supportsPitchMatching == false
                  ? 'Tự động chọn giọng nam/nữ'
                  : 'Tự động theo cao độ và lời thoại',
            ),
            voiceModeOptions.last,
          ],
          onChanged: (v) => _update((c) => c.copyWith(voiceMode: v)),
        ),
        Row(
          children: [
            Expanded(
              child: SelectField(
                label: 'Giọng đọc',
                value: config.voiceKey,
                enabled: voices.isNotEmpty,
                options: [
                  for (final v in voices)
                    (value: v.key, label: v.displayLabel),
                ],
                onChanged: (key) {
                  final voice = voices.firstWhere((v) => v.key == key);
                  _update((c) => c.copyWith(
                        voiceKey: voice.key,
                        voiceGender: voice.gender,
                      ));
                },
              ),
            ),
            if (voicePreviewProviders.contains(config.ttsProvider) &&
                voices.isNotEmpty) ...[
              const SizedBox(width: 8),
              VoicePreviewButton(
                provider: config.ttsProvider,
                voiceKey: config.voiceKey,
              ),
            ],
          ],
        ),
        SelectField(
          label: 'Chất giọng',
          value: config.voiceStyle,
          options: voiceStyleOptions,
          onChanged: (v) => _update((c) => c.copyWith(voiceStyle: v)),
        ),
        SliderField(
          label: 'Tốc độ đọc',
          value: config.speechRate,
          min: 0.75,
          max: 1.5,
          divisions: 75,
          format: (v) => '${v.toStringAsFixed(2)}×',
          onChanged: (v) => _update(
            (c) => c.copyWith(speechRate: double.parse(v.toStringAsFixed(2))),
          ),
        ),
      ],
    );
  }

  Widget _logoSection(TranslationConfig config) {
    final hasLogo = ref.watch(brandingSettingsProvider).valueOrNull?.logo != null;
    return FormSection(
      title: 'Logo thương hiệu',
      subtitle: 'Chèn logo của tài khoản vào video thành phẩm',
      icon: Icons.branding_watermark_outlined,
      children: [
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text(
            'Chèn logo thương hiệu',
            style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600),
          ),
          subtitle: Text(
            hasLogo
                ? 'Tự nhận diện logo có sẵn trong video, không thấy thì đặt ở góc mặc định'
                : 'Chưa có logo — tải logo ở mục Logo & cấu hình chèn logo',
          ),
          value: hasLogo && config.applyLogo,
          onChanged: hasLogo ? (v) => _update((c) => c.copyWith(applyLogo: v)) : null,
        ),
        if (!hasLogo)
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton(
              onPressed: () => context.push('/branding'),
              child: const Text('Tải logo lên'),
            ),
          ),
      ],
    );
  }

  Widget _audioSection(TranslationConfig config) {
    String percent(double v) => '${(v * 100).round()}%';
    return FormSection(
      title: 'Trộn âm thanh (Audio Mix)',
      subtitle: 'Điều chỉnh tỷ lệ âm lượng gốc và nhạc nền',
      icon: Icons.graphic_eq_rounded,
      children: [
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text(
            'Giữ âm thanh gốc dưới giọng thuyết minh',
            style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600),
          ),
          value: config.keepOriginalAudio,
          onChanged: config.hasVoice
              ? (v) => _update((c) => c.copyWith(keepOriginalAudio: v))
              : null,
        ),
        SliderField(
          label: 'Âm lượng tiếng gốc',
          value: config.originalAudioVolume,
          min: 0,
          max: 1,
          divisions: 100,
          format: percent,
          enabled: config.hasVoice,
          onChanged: (v) => _update(
            (c) => c.copyWith(
              originalAudioVolume: double.parse(v.toStringAsFixed(2)),
            ),
          ),
        ),
        if (config.needsMusic)
          SliderField(
            label: 'Âm lượng nhạc nền',
            value: config.musicVolume,
            min: 0,
            max: 1,
            divisions: 100,
            format: percent,
            onChanged: (v) => _update(
              (c) =>
                  c.copyWith(musicVolume: double.parse(v.toStringAsFixed(2))),
            ),
          ),
      ],
    );
  }

  Widget _expandable({
    required String title,
    required IconData icon,
    required List<Widget> children,
  }) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.line),
        boxShadow: AppColors.cardShadow,
      ),
      clipBehavior: Clip.antiAlias,
      child: ExpansionTile(
        leading: Icon(icon, color: AppColors.brand),
        title: Text(
          title,
          style: const TextStyle(
            fontSize: 15,
            fontWeight: FontWeight.w800,
            color: AppColors.ink,
          ),
        ),
        childrenPadding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
        children: [
          for (var i = 0; i < children.length; i++) ...[
            if (i > 0) const SizedBox(height: 12),
            children[i],
          ],
        ],
      ),
    );
  }

  Widget _subtitleStyleSection(TranslationConfig config) {
    final style = config.subtitle;
    void set(SubtitleStyle Function(SubtitleStyle) change) =>
        _update((c) => c.copyWith(subtitle: change(c.subtitle)));
    return FormSection(
      stepNumber: 3,
      title: 'Vị trí & Kiểu phụ đề',
      subtitle: 'Tùy chỉnh vị trí hiển thị, màu sắc và xem trước phụ đề',
      children: [
        _SubtitlePreviewMonitor(style: style),
        SelectField(
          label: 'Vị trí phụ đề',
          value: style.placementMode,
          options: placementOptions,
          onChanged: (v) => set((s) => s.copyWith(placementMode: v)),
        ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text(
            'Khớp cỡ chữ trên video',
            style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600),
          ),
          value: style.matchSourceSize,
          onChanged: (v) => set((s) => s.copyWith(matchSourceSize: v)),
        ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text(
            'Che chữ nguồn khi thay thế',
            style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600),
          ),
          value: style.maskOriginal,
          onChanged: (v) => set((s) => s.copyWith(maskOriginal: v)),
        ),
        SliderField(
          label: 'Cỡ chữ nhỏ nhất',
          value: style.minFontSize.toDouble(),
          min: 8,
          max: 100,
          divisions: 92,
          format: (v) => '${v.round()} px',
          onChanged: (v) => set((s) => s.copyWith(minFontSize: v.round())),
        ),
        SliderField(
          label: 'Cỡ chữ lớn nhất',
          value: style.maxFontSize.toDouble(),
          min: 12,
          max: 200,
          divisions: 188,
          format: (v) => '${v.round()} px',
          onChanged: (v) => set((s) => s.copyWith(maxFontSize: v.round())),
        ),
        SliderField(
          label: 'Khoảng cách phía dưới',
          value: style.positionGap.toDouble(),
          min: 0,
          max: 300,
          divisions: 300,
          format: (v) => '${v.round()} px',
          onChanged: (v) => set((s) => s.copyWith(positionGap: v.round())),
        ),
        ColorChoiceField(
          label: 'Màu chữ',
          value: style.textColor,
          onChanged: (v) => set((s) => s.copyWith(textColor: v)),
        ),
        ColorChoiceField(
          label: 'Màu nền',
          value: style.backgroundColor,
          onChanged: (v) => set((s) => s.copyWith(backgroundColor: v)),
        ),
        SelectField(
          label: 'Kiểu nền',
          value: style.backgroundStyle,
          options: backgroundStyleOptions,
          onChanged: (v) => set((s) => s.copyWith(backgroundStyle: v)),
        ),
      ],
    );
  }

  Widget _tiktokSection(TranslationConfig config) {
    return FormSection(
      stepNumber: 4,
      title: 'Nội dung đăng TikTok',
      subtitle: 'Tự động sinh caption tóm tắt và bộ hashtag xu hướng',
      children: [
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text(
            'Tự tạo caption và hashtag',
            style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600),
          ),
          value: config.generateTiktokPost,
          onChanged: (v) => _update((c) => c.copyWith(generateTiktokPost: v)),
        ),
        SliderField(
          label: 'Độ dài tóm tắt tối đa',
          value: config.tiktokMaxSummaryChars.toDouble(),
          min: 80,
          max: 1500,
          divisions: 142,
          format: (v) => '${v.round()} ký tự',
          onChanged: (v) =>
              _update((c) => c.copyWith(tiktokMaxSummaryChars: v.round())),
        ),
        SliderField(
          label: 'Số hashtag tối đa',
          value: config.tiktokHashtagCount.toDouble(),
          min: 0,
          max: 5,
          divisions: 5,
          format: (v) => '${v.round()}',
          onChanged: (v) =>
              _update((c) => c.copyWith(tiktokHashtagCount: v.round())),
        ),
      ],
    );
  }

  Widget _ocrSection(TranslationConfig config) {
    return _expandable(
      title: 'OCR nâng cao',
      icon: Icons.document_scanner_outlined,
      children: [
        SliderField(
          label: 'Frame OCR mỗi giây',
          value: config.ocrSampleFps,
          min: 1,
          max: 15,
          divisions: 28,
          format: (v) => v.toStringAsFixed(1),
          onChanged: (v) => _update((c) => c.copyWith(ocrSampleFps: v)),
        ),
        SliderField(
          label: 'Mép trên vùng OCR',
          value: config.ocrRoiTop,
          min: 0,
          max: 0.95,
          divisions: 95,
          onChanged: (v) => _update(
            (c) => c.copyWith(ocrRoiTop: double.parse(v.toStringAsFixed(2))),
          ),
        ),
        SliderField(
          label: 'Mép dưới vùng OCR',
          value: config.ocrRoiBottom,
          min: 0.05,
          max: 1,
          divisions: 95,
          onChanged: (v) => _update(
            (c) =>
                c.copyWith(ocrRoiBottom: double.parse(v.toStringAsFixed(2))),
          ),
        ),
      ],
    );
  }

  Widget _submitCard(TranslateFormState state) {
    final config = state.config;
    final modeLabel = switch (config.mode) {
      1 => 'Chỉ phụ đề',
      2 => 'Phụ đề + Thuyết minh',
      4 => 'Chỉ tải video',
      _ => 'Phụ đề + Dub + Nhạc',
    };

    return StudioCard(
      topAccentColor: AppColors.brand,
      padding: const EdgeInsets.all(16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const StudioEyebrow(
                label: 'SẴN SÀNG KHỞI CHẠY',
                icon: Icons.rocket_launch_outlined,
              ),
              const Spacer(),
              Text(
                '${state.sourceCount}/50 nguồn',
                style: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w800,
                  color: AppColors.inkSoft,
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            decoration: BoxDecoration(
              color: AppColors.surfaceSubtle,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: AppColors.line),
            ),
            child: Row(
              children: [
                Expanded(
                  child: _SummaryItem(
                    label: 'Chế độ',
                    value: modeLabel,
                  ),
                ),
                Container(width: 1, height: 26, color: AppColors.line),
                const SizedBox(width: 10),
                Expanded(
                  child: _SummaryItem(
                    label: 'Ngôn ngữ',
                    value: config.downloadOnly
                        ? 'Giữ nguyên'
                        : '${config.sourceLanguage.toUpperCase()} → ${config.targetLanguage.toUpperCase()}',
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 14),
          if (state.error != null)
            Container(
              margin: const EdgeInsets.only(bottom: 12),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
              decoration: BoxDecoration(
                color: AppColors.redSoft,
                borderRadius: BorderRadius.circular(11),
                border: Border.all(color: AppColors.redBorder),
              ),
              child: Text(
                state.error!,
                style: const TextStyle(
                  color: AppColors.redText,
                  fontWeight: FontWeight.w600,
                  fontSize: 12.5,
                ),
              ),
            ),
          if (state.busy) ...[
            Text(
              state.phase == UploadPhase.creating
                  ? 'Tải lên xong. Đang tạo công việc…'
                  : 'Đang tải lên ${(state.progress * 100).round()}%',
              style: const TextStyle(
                fontWeight: FontWeight.w700,
                fontSize: 12.5,
              ),
            ),
            const SizedBox(height: 8),
            LinearProgressIndicator(
              value: state.phase == UploadPhase.creating
                  ? null
                  : state.progress,
            ),
            const SizedBox(height: 10),
            OutlinedButton(
              onPressed: _form.cancel,
              child: const Text('Hủy tải lên'),
            ),
          ] else
            StudioGradientButton(
              onPressed: _submit,
              icon: Icons.rocket_launch_rounded,
              label: state.error != null && state.sourceCount > 0
                  ? 'Thử lại'
                  : 'Bắt đầu xử lý batch (${state.sourceCount} nguồn)',
            ),
        ],
      ),
    );
  }

  static String _formatSize(int bytes) {
    if (bytes >= 1 << 30) {
      return '${(bytes / (1 << 30)).toStringAsFixed(2)} GB';
    }
    if (bytes >= 1 << 20) {
      return '${(bytes / (1 << 20)).toStringAsFixed(1)} MB';
    }
    return '${(bytes / 1024).toStringAsFixed(0)} KB';
  }
}

class _ModeChoiceCard extends StatelessWidget {
  const _ModeChoiceCard({
    required this.title,
    required this.badge,
    required this.description,
    required this.icon,
    required this.selected,
    required this.onTap,
  });

  final String title;
  final String badge;
  final String description;
  final IconData icon;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(14),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: selected ? AppColors.brandTint : AppColors.surface,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(
              color: selected ? AppColors.brand : AppColors.line,
              width: selected ? 1.5 : 1.0,
            ),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 36,
                height: 36,
                decoration: BoxDecoration(
                  color: selected ? AppColors.brand : AppColors.surfaceMuted,
                  borderRadius: BorderRadius.circular(10),
                ),
                alignment: Alignment.center,
                child: Icon(
                  icon,
                  size: 19,
                  color: selected ? Colors.white : AppColors.inkSoft,
                ),
              ),
              const SizedBox(width: 11),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Wrap(
                      spacing: 6,
                      runSpacing: 4,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        Text(
                          title,
                          style: TextStyle(
                            fontSize: 13.8,
                            fontWeight: FontWeight.w800,
                            color:
                                selected ? AppColors.brandDark : AppColors.ink,
                          ),
                        ),
                        Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 7,
                            vertical: 2,
                          ),
                          decoration: BoxDecoration(
                            color: selected
                                ? AppColors.brandSoft
                                : AppColors.surfaceMuted,
                            borderRadius: BorderRadius.circular(999),
                            border: Border.all(
                              color: selected
                                  ? AppColors.brandBorder
                                  : AppColors.line,
                            ),
                          ),
                          child: Text(
                            badge,
                            style: TextStyle(
                              fontSize: 10,
                              fontWeight: FontWeight.w800,
                              color: selected
                                  ? AppColors.brandDark
                                  : AppColors.muted,
                            ),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 3),
                    Text(
                      description,
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.muted,
                        height: 1.35,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 6),
              Icon(
                selected
                    ? Icons.radio_button_checked_rounded
                    : Icons.radio_button_off_rounded,
                size: 20,
                color: selected ? AppColors.brand : AppColors.mutedLight,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _SummaryItem extends StatelessWidget {
  const _SummaryItem({
    required this.label,
    required this.value,
  });

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          label.toUpperCase(),
          style: const TextStyle(
            fontSize: 9.5,
            fontWeight: FontWeight.w800,
            letterSpacing: 0.4,
            color: AppColors.muted,
          ),
        ),
        const SizedBox(height: 2),
        Text(
          value,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            fontSize: 12.5,
            fontWeight: FontWeight.w800,
            color: AppColors.ink,
          ),
        ),
      ],
    );
  }
}

class _SubtitlePreviewMonitor extends StatelessWidget {
  const _SubtitlePreviewMonitor({required this.style});

  final SubtitleStyle style;

  @override
  Widget build(BuildContext context) {
    final fg = ColorChoiceField.parse(style.textColor);
    final bg = ColorChoiceField.parse(style.backgroundColor);

    return Container(
      height: 124,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        gradient: AppColors.subtitlePreviewGradient,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: const Color(0xFF334155)),
      ),
      child: Stack(
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: const Text(
                  'XEM TRƯỚC PHỤ ĐỀ · 16:9',
                  style: TextStyle(
                    fontSize: 9.5,
                    fontWeight: FontWeight.w800,
                    color: Colors.white70,
                    letterSpacing: 0.5,
                  ),
                ),
              ),
              const Spacer(),
              Text(
                '${style.minFontSize}–${style.maxFontSize}px',
                style: const TextStyle(
                  fontSize: 10,
                  fontWeight: FontWeight.w700,
                  color: Colors.white60,
                ),
              ),
            ],
          ),
          Align(
            alignment: Alignment.bottomCenter,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              decoration: BoxDecoration(
                color: style.backgroundStyle == 'none'
                    ? Colors.transparent
                    : bg.withValues(alpha: 0.78),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                'Xin chào! Đây là phụ đề tiếng Việt tự động',
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: fg,
                  fontSize: 13.5,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
