import 'dart:ui';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:path_provider/path_provider.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/account.dart';
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
  final _scrollController = ScrollController();
  late final AppLifecycleListener _lifecycle;
  int _activeStep = 1;
  bool _atBottomSubmit = false;
  List<String> _clipboardLinks = const [];
  String? _dismissedClipboardRaw;

  final _step1Key = GlobalKey();
  final _step2Key = GlobalKey();
  final _step3Key = GlobalKey();
  final _step4Key = GlobalKey();
  final _submitCardKey = GlobalKey();

  TranslateFormController get _form => ref.read(translateFormProvider.notifier);

  @override
  void initState() {
    super.initState();
    final state = ref.read(translateFormProvider);
    _links = TextEditingController(text: state.linksText);
    _batchName = TextEditingController(text: state.config.batchName);
    _scrollController.addListener(_onScroll);
    _lifecycle = AppLifecycleListener(onResume: _checkClipboard);
    WidgetsBinding.instance.addPostFrameCallback((_) => _checkClipboard());
  }

  @override
  void dispose() {
    _lifecycle.dispose();
    _scrollController.removeListener(_onScroll);
    _scrollController.dispose();
    _links.dispose();
    _batchName.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!mounted) return;
    var nextStep = 1;
    final viewportTop = 160.0;
    for (final entry in [
      (step: 1, key: _step1Key),
      (step: 2, key: _step2Key),
      (step: 3, key: _step3Key),
      (step: 4, key: _step4Key),
    ]) {
      final ctx = entry.key.currentContext;
      if (ctx == null) continue;
      final box = ctx.findRenderObject();
      if (box is RenderBox && box.hasSize) {
        final dy = box.localToGlobal(Offset.zero).dy;
        if (dy <= viewportTop) nextStep = entry.step;
      }
    }
    var submitVisible = false;
    final submitCtx = _submitCardKey.currentContext;
    if (submitCtx != null) {
      final box = submitCtx.findRenderObject();
      if (box is RenderBox && box.hasSize) {
        final dy = box.localToGlobal(Offset.zero).dy;
        final screenHeight = MediaQuery.sizeOf(context).height;
        submitVisible = dy < screenHeight - 100;
      }
    }
    if (nextStep != _activeStep || submitVisible != _atBottomSubmit) {
      setState(() {
        _activeStep = nextStep;
        _atBottomSubmit = submitVisible;
      });
    }
  }

  Future<void> _checkClipboard() async {
    try {
      final data = await Clipboard.getData(Clipboard.kTextPlain);
      final raw = data?.text?.trim() ?? '';
      if (!mounted) return;
      if (raw.isEmpty || raw == _dismissedClipboardRaw) {
        if (_clipboardLinks.isNotEmpty) {
          setState(() => _clipboardLinks = const []);
        }
        return;
      }
      final existing = ref.read(translateFormProvider).links.toSet();
      final fresh = extractLinks(
        raw,
      ).where((l) => !existing.contains(l)).toList();
      if (fresh.length != _clipboardLinks.length ||
          !fresh.every(_clipboardLinks.contains)) {
        setState(() => _clipboardLinks = fresh);
      }
    } catch (_) {}
  }

  Future<void> _pasteFromClipboard() async {
    HapticFeedback.selectionClick();
    try {
      final data = await Clipboard.getData(Clipboard.kTextPlain);
      final raw = data?.text?.trim() ?? '';
      if (!mounted) return;
      if (raw.isEmpty) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('Bộ nhớ tạm đang trống')));
        return;
      }
      final added = _form.appendLinksText(raw);
      if (added > 0) {
        _links.text = ref.read(translateFormProvider).linksText;
        setState(() => _clipboardLinks = const []);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Đã dán $added link video hợp lệ')),
        );
      } else {
        // Still paste raw text if no existing links so user sees what they pasted,
        // or notify if links were already added.
        final found = extractLinks(raw);
        if (found.isNotEmpty) {
          setState(() => _clipboardLinks = const []);
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
              content: Text('Link trong bộ nhớ tạm đã có trong danh sách'),
            ),
          );
        } else {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
              content: Text(
                'Không tìm thấy link TikTok, Douyin, YouTube, Facebook hoặc Google Drive trong bộ nhớ tạm',
              ),
            ),
          );
        }
      }
    } catch (_) {}
  }

  void _removeLink(String url) {
    HapticFeedback.selectionClick();
    _form.removeLink(url);
    _links.text = ref.read(translateFormProvider).linksText;
  }

  void _clearLinks() {
    HapticFeedback.selectionClick();
    _form.clearLinks();
    _links.clear();
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
    final formState = ref.read(translateFormProvider);
    final count = formState.sourceCount;

    final err = _form.validate();
    if (err != null) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(err), behavior: SnackBarBehavior.floating),
        );
      }
      if (count == 0) {
        _jumpToStep(1, _step1Key);
      }
      return;
    }

    final confirmed = await _showConfirmJobSheet(formState);
    if (confirmed != true || !mounted) return;

    if (_form.submit() == null) {
      final submitErr = ref.read(translateFormProvider).error;
      if (submitErr != null && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(submitErr),
            behavior: SnackBarBehavior.floating,
          ),
        );
      }
      return;
    }
    _links.clear();
    _batchName.text = ref.read(translateFormProvider).config.batchName;
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            'Đã đưa $count video vào tiến trình, đang tải dưới nền',
          ),
          behavior: SnackBarBehavior.floating,
        ),
      );
      context.go('/jobs');
    }
  }

  Future<bool?> _showConfirmJobSheet(TranslateFormState state) {
    final config = state.config;
    final modeLabel = switch (config.mode) {
      1 => 'Phụ đề',
      2 => 'Phụ đề + Voice',
      4 => 'Chỉ tải video',
      _ => 'Voice + Nhạc',
    };
    final langLabel = config.downloadOnly
        ? 'Chỉ tải video gốc'
        : '${_langName(config.sourceLanguage)} → ${_langName(config.targetLanguage)}';

    String? voiceName;
    if (config.hasVoice) {
      final providers = ref.read(ttsProvidersProvider).valueOrNull;
      final provider = providers
          ?.where((p) => p.key == config.ttsProvider)
          .firstOrNull;
      final voice = provider?.voiceChoices
          .where((v) => v.key == config.voiceKey)
          .firstOrNull;
      voiceName = voice?.label ?? config.voiceKey;
    }

    final batchName = _batchName.text.trim().isNotEmpty
        ? _batchName.text.trim()
        : (config.batchName.isNotEmpty ? config.batchName : 'Batch video mới');

    final allSources = <({bool isFile, String text})>[
      for (final f in state.files) (isFile: true, text: f.name),
      for (final l in state.links) (isFile: false, text: l),
    ];

    return showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (sheetContext) {
        return Container(
          margin: const EdgeInsets.fromLTRB(12, 0, 12, 16),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(24),
            border: Border.all(color: AppColors.brandBorder),
            boxShadow: const [
              BoxShadow(
                color: Color(0x2E0F172A),
                blurRadius: 34,
                offset: Offset(0, 14),
              ),
            ],
          ),
          child: SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(18, 14, 18, 18),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Center(
                    child: Container(
                      width: 38,
                      height: 4,
                      decoration: BoxDecoration(
                        color: const Color(0xFFCBD5E1),
                        borderRadius: BorderRadius.circular(999),
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  Row(
                    children: [
                      Container(
                        width: 44,
                        height: 44,
                        decoration: BoxDecoration(
                          gradient: AppColors.brandGradient,
                          shape: BoxShape.circle,
                          boxShadow: const [
                            BoxShadow(
                              color: Color(0x4DEA580C),
                              blurRadius: 10,
                              offset: Offset(0, 4),
                            ),
                          ],
                        ),
                        child: const Icon(
                          Icons.rocket_launch_rounded,
                          color: Colors.white,
                          size: 22,
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text(
                              'Xác nhận tạo tiến trình',
                              style: TextStyle(
                                fontSize: 16.5,
                                fontWeight: FontWeight.w800,
                                color: AppColors.ink,
                                letterSpacing: -0.2,
                              ),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              'Sẵn sàng xử lý ${state.sourceCount} video theo cấu hình sau',
                              style: const TextStyle(
                                fontSize: 12.5,
                                color: AppColors.muted,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 16),
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: AppColors.surfaceSubtle,
                      borderRadius: BorderRadius.circular(14),
                      border: Border.all(color: AppColors.line),
                    ),
                    child: Column(
                      children: [
                        Row(
                          children: [
                            Expanded(
                              child: _SummaryItem(
                                label: 'Số lượng nguồn',
                                value: '${state.sourceCount} video',
                              ),
                            ),
                            Container(
                              width: 1,
                              height: 26,
                              color: AppColors.line,
                            ),
                            const SizedBox(width: 10),
                            Expanded(
                              child: _SummaryItem(
                                label: 'Chế độ',
                                value: modeLabel,
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 10),
                        Container(height: 1, color: AppColors.line),
                        const SizedBox(height: 10),
                        Row(
                          children: [
                            Expanded(
                              child: _SummaryItem(
                                label: 'Ngôn ngữ',
                                value: langLabel,
                              ),
                            ),
                            if (voiceName != null) ...[
                              Container(
                                width: 1,
                                height: 26,
                                color: AppColors.line,
                              ),
                              const SizedBox(width: 10),
                              Expanded(
                                child: _SummaryItem(
                                  label: 'Giọng đọc',
                                  value: voiceName,
                                ),
                              ),
                            ],
                          ],
                        ),
                        if (batchName.isNotEmpty) ...[
                          const SizedBox(height: 10),
                          Container(height: 1, color: AppColors.line),
                          const SizedBox(height: 10),
                          Row(
                            children: [
                              const Text(
                                'Tên batch: ',
                                style: TextStyle(
                                  fontSize: 11.5,
                                  color: AppColors.muted,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              const SizedBox(width: 6),
                              Expanded(
                                child: Text(
                                  batchName,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(
                                    fontSize: 12.5,
                                    fontWeight: FontWeight.w700,
                                    color: AppColors.ink,
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ],
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),
                  // Sources preview
                  Container(
                    constraints: const BoxConstraints(maxHeight: 110),
                    padding: const EdgeInsets.symmetric(
                      horizontal: 10,
                      vertical: 8,
                    ),
                    decoration: BoxDecoration(
                      color: const Color(0xFFF8FAFC),
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: const Color(0xFFE2E8F0)),
                    ),
                    child: ListView.separated(
                      shrinkWrap: true,
                      itemCount: allSources.length > 3 ? 4 : allSources.length,
                      separatorBuilder: (_, __) =>
                          const Divider(height: 8, thickness: 0.5),
                      itemBuilder: (context, index) {
                        if (index == 3 && allSources.length > 3) {
                          return Text(
                            '+ ${allSources.length - 3} nguồn khác...',
                            style: const TextStyle(
                              fontSize: 11.5,
                              color: AppColors.brand,
                              fontWeight: FontWeight.w700,
                            ),
                          );
                        }
                        final item = allSources[index];
                        return Row(
                          children: [
                            Icon(
                              item.isFile
                                  ? Icons.movie_outlined
                                  : Icons.link_rounded,
                              size: 15,
                              color: AppColors.muted,
                            ),
                            const SizedBox(width: 6),
                            Expanded(
                              child: Text(
                                item.text,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                  fontSize: 12,
                                  color: AppColors.ink,
                                  fontWeight: FontWeight.w500,
                                ),
                              ),
                            ),
                          ],
                        );
                      },
                    ),
                  ),
                  const SizedBox(height: 12),
                  // Assurance box
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 10,
                      vertical: 8,
                    ),
                    decoration: BoxDecoration(
                      color: const Color(0xFFF0FDF4),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: const Color(0xFFBBF7D0)),
                    ),
                    child: const Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(
                          Icons.check_circle_rounded,
                          size: 16,
                          color: Color(0xFF16A34A),
                        ),
                        SizedBox(width: 7),
                        Expanded(
                          child: Text(
                            'Video sẽ chạy ngầm tự động trên máy chủ. Bạn có thể theo dõi trong tab Tiến trình bất cứ lúc nào.',
                            style: TextStyle(
                              fontSize: 11.5,
                              color: Color(0xFF15803D),
                              fontWeight: FontWeight.w500,
                              height: 1.35,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 16),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton(
                          onPressed: () => Navigator.pop(sheetContext, false),
                          style: OutlinedButton.styleFrom(
                            padding: const EdgeInsets.symmetric(vertical: 12),
                            side: const BorderSide(color: AppColors.line),
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(12),
                            ),
                          ),
                          child: const Text(
                            'Kiểm tra lại',
                            style: TextStyle(
                              color: AppColors.inkSoft,
                              fontWeight: FontWeight.w700,
                              fontSize: 13.5,
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: StudioGradientButton(
                          padding: const EdgeInsets.symmetric(vertical: 12),
                          onPressed: () => Navigator.pop(sheetContext, true),
                          icon: Icons.rocket_launch_rounded,
                          label: 'Bắt đầu ngay',
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        );
      },
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
        translationProvider: pickTranslationProvider(
          next.translationProvider,
          translation,
        ),
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
      WidgetsBinding.instance.addPostFrameCallback(
        (_) => _form.updateConfig(next),
      );
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
    final account = ref.watch(sessionProvider).valueOrNull;
    final readOnly = account?.isReadOnly ?? true;
    final canTranslateImages =
        account?.hasFeature(PlanFeature.imageTranslation) ?? false;
    if (readOnly) {
      return Scaffold(
        backgroundColor: AppColors.bg,
        appBar: const StudioTopBar(activeStudioPill: StudioCategoryPill.video),
        body: Padding(
          padding: EdgeInsets.only(bottom: studioDockClearance(context)),
          child: StudioEmptyState(
            icon: Icons.lock_outline_rounded,
            title: 'Tài khoản chỉ xem',
            subtitle: 'Tài khoản chỉ xem không thể tạo công việc mới.',
            action: canTranslateImages
                ? OutlinedButton.icon(
                    onPressed: () => context.go('/translate/image'),
                    icon: const Icon(Icons.image_search_rounded, size: 18),
                    label: const Text('Dịch ảnh chụp màn hình'),
                  )
                : null,
          ),
        ),
      );
    }

    final state = ref.watch(translateFormProvider);
    final config = state.config;
    final translation = ref.watch(translationProvidersProvider).valueOrNull;
    final tts = ref.watch(ttsProvidersProvider).valueOrNull;
    _reconcile(config, translation, tts);

    // The quick start bar mirrors the web UI: it only appears when the user
    // has pasted valid links or added files (state.sourceCount > 0), and hides
    // when scrolled down to the full submit card.
    final showQuickBar = !_atBottomSubmit && state.sourceCount > 0;

    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: const StudioTopBar(activeStudioPill: StudioCategoryPill.video),
      body: Stack(
        children: [
          ListView(
            controller: _scrollController,
            padding: EdgeInsets.fromLTRB(
              14,
              14,
              14,
              studioDockBottomPadding(context, extra: showQuickBar ? 82 : 24),
            ),
            children: [
              _heroHeader(),
              const SizedBox(height: 14),
              _workflowStepsNav(),
              const SizedBox(height: 14),
              KeyedSubtree(key: _step1Key, child: _sourcesSection(state)),
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
                if (config.hasVoice) ...[
                  _voiceSection(config, tts),
                  _audioSection(config),
                ] else
                  _mode1VoiceNotice(),
              ],
              _logoSection(config),
              if (!config.downloadOnly) ...[
                KeyedSubtree(
                  key: _step3Key,
                  child: _subtitleStyleSection(config),
                ),
                KeyedSubtree(key: _step4Key, child: _tiktokSection(config)),
                _ocrSection(config),
              ],
              const SizedBox(height: 4),
              KeyedSubtree(key: _submitCardKey, child: _submitCard(state)),
            ],
          ),
          Positioned(
            left: 0,
            right: 0,
            bottom: studioDockClearance(context),
            child: AnimatedSlide(
              duration: const Duration(milliseconds: 260),
              curve: Curves.easeOutCubic,
              offset: showQuickBar ? Offset.zero : const Offset(0, 0.4),
              child: AnimatedOpacity(
                duration: const Duration(milliseconds: 220),
                curve: Curves.easeOut,
                opacity: showQuickBar ? 1.0 : 0.0,
                child: IgnorePointer(
                  ignoring: !showQuickBar,
                  child: Center(
                    child: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 420),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 14),
                        child: _quickSubmitBar(state),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
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
      (step: 2, label: 'Cấu hình xử lý', sub: 'Chế độ & giọng', key: _step2Key),
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
    final steps = downloadOnly
        ? allSteps.where((s) => s.step <= 2).toList()
        : allSteps;

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
          // Download-only keeps just steps 1-2: indexing 2 and 3 would throw.
          if (steps.length > 2) ...[
            const SizedBox(height: 6),
            Row(
              children: [
                Expanded(child: _stepNavItem(steps[2])),
                const SizedBox(width: 6),
                Expanded(child: _stepNavItem(steps[3])),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Widget _stepNavItem(
    ({int step, String label, String sub, GlobalKey key}) item,
  ) {
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
    final hasInvalidLinkText =
        state.linksText.trim().isNotEmpty && links.isEmpty;

    return FormSection(
      stepNumber: 1,
      title: 'Thêm video nguồn',
      subtitle:
          'Tải file từ thiết bị hoặc dán liên kết TikTok, Douyin, YouTube, Facebook hoặc Google Drive — tối đa 50 video mỗi batch.',
      trailing: StudioEyebrow(label: '${state.sourceCount}/50 nguồn'),
      children: [
        if (_clipboardLinks.isNotEmpty)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                colors: [Color(0xFFF0FDFA), Color(0xFFCCFBF1)],
              ),
              borderRadius: BorderRadius.circular(13),
              border: Border.all(color: AppColors.tealBorder),
            ),
            child: Row(
              children: [
                const Icon(
                  Icons.content_paste_go_rounded,
                  size: 19,
                  color: AppColors.tealDark,
                ),
                const SizedBox(width: 9),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Phát hiện ${_clipboardLinks.length} link video vừa sao chép',
                        style: const TextStyle(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w800,
                          color: AppColors.tealDark,
                        ),
                      ),
                      Text(
                        _clipboardLinks.first,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 11,
                          color: AppColors.tealText,
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                FilledButton.tonal(
                  style: FilledButton.styleFrom(
                    minimumSize: Size.zero,
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    visualDensity: VisualDensity.compact,
                    backgroundColor: AppColors.teal,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(
                      horizontal: 11,
                      vertical: 7,
                    ),
                  ),
                  onPressed: _pasteFromClipboard,
                  child: const Text(
                    'Dán ngay',
                    style: TextStyle(
                      fontSize: 11.5,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ),
                IconButton(
                  visualDensity: VisualDensity.compact,
                  tooltip: 'Ẩn gợi ý',
                  icon: const Icon(
                    Icons.close_rounded,
                    size: 16,
                    color: AppColors.tealDark,
                  ),
                  onPressed: () async {
                    final clip = await Clipboard.getData(Clipboard.kTextPlain);
                    if (!mounted) return;
                    setState(() {
                      _dismissedClipboardRaw = clip?.text?.trim();
                      _clipboardLinks = const [];
                    });
                  },
                ),
              ],
            ),
          ),
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
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          '${state.files.length} file đã chọn',
                          style: const TextStyle(
                            fontSize: 11.5,
                            fontWeight: FontWeight.w700,
                            color: AppColors.teal,
                          ),
                        ),
                        if (state.files.length > 1) ...[
                          const SizedBox(width: 8),
                          InkWell(
                            onTap: _form.clearFiles,
                            borderRadius: BorderRadius.circular(6),
                            child: const Padding(
                              padding: EdgeInsets.symmetric(
                                horizontal: 4,
                                vertical: 2,
                              ),
                              child: Text(
                                'Xóa hết',
                                style: TextStyle(
                                  fontSize: 11,
                                  fontWeight: FontWeight.w800,
                                  color: AppColors.redDark,
                                ),
                              ),
                            ),
                          ),
                        ],
                      ],
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
                  // Flexible: narrow phones or large system font sizes.
                  const Expanded(
                    child: Text(
                      '1B · TỪ LIÊN KẾT MẠNG XÃ HỘI',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w800,
                        letterSpacing: 0.5,
                        color: AppColors.teal,
                      ),
                    ),
                  ),
                  const SizedBox(width: 6),
                  InkWell(
                    onTap: _pasteFromClipboard,
                    borderRadius: BorderRadius.circular(999),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 9,
                        vertical: 4,
                      ),
                      decoration: BoxDecoration(
                        color: AppColors.tealSoft,
                        borderRadius: BorderRadius.circular(999),
                        border: Border.all(color: AppColors.tealBorder),
                      ),
                      child: const Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            Icons.content_paste_rounded,
                            size: 13,
                            color: AppColors.tealDark,
                          ),
                          SizedBox(width: 4),
                          Text(
                            'Dán nhanh',
                            style: TextStyle(
                              fontSize: 11,
                              fontWeight: FontWeight.w800,
                              color: AppColors.tealDark,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                  if (state.linksText.isNotEmpty) ...[
                    const SizedBox(width: 6),
                    InkWell(
                      onTap: _clearLinks,
                      borderRadius: BorderRadius.circular(999),
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 4,
                        ),
                        decoration: BoxDecoration(
                          color: Colors.white,
                          borderRadius: BorderRadius.circular(999),
                          border: Border.all(color: AppColors.lineStrong),
                        ),
                        child: const Text(
                          'Xóa',
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w700,
                            color: AppColors.muted,
                          ),
                        ),
                      ),
                    ),
                  ],
                  if (links.isNotEmpty) ...[
                    const SizedBox(width: 6),
                    StudioStatusPill(
                      status: 'ready',
                      label: '${links.length} link',
                      compact: true,
                    ),
                  ],
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
                      'Dán link hoặc đoạn chia sẻ TikTok, Douyin, YouTube, Facebook, Google Drive…',
                  counterText: '',
                  prefixIcon: Icon(Icons.link_rounded, color: AppColors.teal),
                ),
                onChanged: _form.setLinks,
              ),
              if (hasInvalidLinkText) ...[
                const SizedBox(height: 8),
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 10,
                    vertical: 7,
                  ),
                  decoration: BoxDecoration(
                    color: AppColors.amberSoft,
                    borderRadius: BorderRadius.circular(9),
                    border: Border.all(color: AppColors.amberBorder),
                  ),
                  child: const Row(
                    children: [
                      Icon(
                        Icons.info_outline_rounded,
                        size: 15,
                        color: AppColors.amberDark,
                      ),
                      SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          'Chưa nhận diện được link TikTok, Douyin, YouTube hoặc Facebook hợp lệ.',
                          style: TextStyle(
                            fontSize: 11.5,
                            fontWeight: FontWeight.w600,
                            color: AppColors.amberText,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
              if (links.isNotEmpty) ...[
                const SizedBox(height: 8),
                for (var i = 0; i < links.length; i++) ...[
                  if (i > 0) const SizedBox(height: 6),
                  _LinkSourceTile(
                    url: links[i],
                    onRemove: () => _removeLink(links[i]),
                  ),
                ],
              ],
            ],
          ),
        ),
        const SizedBox(height: 12),
        const _SmartSourceNotice(),
      ],
    );
  }

  Widget _modeBody(TranslateFormState state) {
    final config = state.config;
    const modes =
        <({int value, String title, String badge, String desc, IconData icon})>[
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
            desc:
                'Tải video gốc từ link hoặc file về, không nhận diện, dịch hay lồng tiếng.',
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
          const SizedBox(height: 12),
          _musicPicker(config, state),
        ],
      ],
    );
  }

  /// Mode 3: a track of the admin-managed library, or the user's own file.
  Widget _musicPicker(TranslationConfig config, TranslateFormState state) {
    final library = ref.watch(musicTracksProvider);
    final tracks = library.valueOrNull ?? const <MusicTrack>[];
    final selected = tracks.any((t) => t.id == config.musicTrackId)
        ? config.musicTrackId
        : '';
    if (library.hasValue &&
        selected.isEmpty &&
        config.musicTrackId.isNotEmpty) {
      // The remembered track was removed from the library: forget it.
      WidgetsBinding.instance.addPostFrameCallback(
        (_) => _form.selectMusicTrack(''),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(
              child: SelectField(
                label: 'Nhạc nền',
                value: selected,
                helper: tracks.isEmpty
                    ? 'Thư viện chưa có nhạc — hãy tải file nhạc của bạn.'
                    : null,
                options: [
                  (value: '', label: 'Tự tải file nhạc của bạn'),
                  for (final t in tracks)
                    (value: t.id, label: '${t.title} (${t.durationLabel})'),
                ],
                onChanged: _form.selectMusicTrack,
              ),
            ),
            if (selected.isNotEmpty)
              AudioPreviewButton(
                sourceKey: selected,
                uri: (repo) => repo.musicTrackAudioUri(selected),
                errorText: 'Không phát được bản nhạc.',
              ),
          ],
        ),
        if (selected.isEmpty) ...[
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: _pickMusic,
            icon: const Icon(Icons.music_note_outlined),
            label: Text(
              state.music?.name ?? 'Chọn file nhạc nền (MP3/WAV/M4A)',
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
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 10),
                      child: Material(
                        color: Colors.transparent,
                        child: InkWell(
                          onTap: config.sourceLanguage != 'auto'
                              ? () {
                                  HapticFeedback.selectionClick();
                                  _form.swapLanguages();
                                }
                              : null,
                          borderRadius: BorderRadius.circular(999),
                          child: Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 10,
                              vertical: 4,
                            ),
                            decoration: BoxDecoration(
                              color: AppColors.brandSoft,
                              borderRadius: BorderRadius.circular(999),
                              border: Border.all(color: AppColors.brandBorder),
                            ),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Icon(
                                  config.sourceLanguage != 'auto'
                                      ? Icons.swap_vert_rounded
                                      : Icons.south_rounded,
                                  size: 13,
                                  color: AppColors.brandDark,
                                ),
                                const SizedBox(width: 4),
                                Text(
                                  config.sourceLanguage != 'auto'
                                      ? 'ĐẢO NGÔN NGỮ'
                                      : 'CHUYỂN ĐỔI SANG',
                                  style: const TextStyle(
                                    fontSize: 9.5,
                                    fontWeight: FontWeight.w800,
                                    letterSpacing: 0.5,
                                    color: AppColors.brandDark,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
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
            _update(
              (c) => c.copyWith(
                ttsProvider: v,
                voiceKey: voice?.key ?? 'female',
                voiceGender: voice?.gender ?? 'female',
              ),
            );
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
                  for (final v in voices) (value: v.key, label: v.displayLabel),
                ],
                onChanged: (key) {
                  final voice = voices.firstWhere((v) => v.key == key);
                  _update(
                    (c) => c.copyWith(
                      voiceKey: voice.key,
                      voiceGender: voice.gender,
                    ),
                  );
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
    final hasLogo =
        ref.watch(brandingSettingsProvider).valueOrNull?.logo != null;
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
          onChanged: hasLogo
              ? (v) => _update((c) => c.copyWith(applyLogo: v))
              : null,
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
              ? (v) => _update(
                  (c) => c.copyWith(
                    keepOriginalAudio: v,
                    originalAudioVolume: v && c.originalAudioVolume <= 0
                        ? 0.25
                        : c.originalAudioVolume,
                  ),
                )
              : null,
        ),
        SliderField(
          label: 'Âm lượng tiếng gốc',
          value: config.keepOriginalAudio ? config.originalAudioVolume : 0,
          min: 0,
          max: 1,
          divisions: 100,
          format: percent,
          enabled: config.hasVoice && config.keepOriginalAudio,
          onChanged: (v) {
            final rounded = double.parse(v.toStringAsFixed(2));
            _update(
              (c) => c.copyWith(
                keepOriginalAudio: rounded > 0,
                originalAudioVolume: rounded,
              ),
            );
          },
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
            (c) => c.copyWith(ocrRoiBottom: double.parse(v.toStringAsFixed(2))),
          ),
        ),
      ],
    );
  }

  Widget _mode1VoiceNotice() {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFFFFFBEB), Color(0xFFFEF3C7)],
        ),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: const Color(0xFFFCD34D)),
        boxShadow: const [
          BoxShadow(
            color: Color(0x12D97706),
            blurRadius: 12,
            offset: Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 38,
                height: 38,
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.85),
                  borderRadius: BorderRadius.circular(11),
                  border: Border.all(color: const Color(0xFFFDE68A)),
                ),
                alignment: Alignment.center,
                child: const Icon(
                  Icons.record_voice_over_outlined,
                  size: 20,
                  color: AppColors.amberDark,
                ),
              ),
              const SizedBox(width: 12),
              const Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Đang giữ 100% âm thanh gốc',
                      style: TextStyle(
                        fontSize: 13.5,
                        fontWeight: FontWeight.w800,
                        color: AppColors.ink,
                        letterSpacing: -0.15,
                      ),
                    ),
                    SizedBox(height: 3),
                    Text(
                      'Chế độ Chỉ phụ đề không tạo giọng đọc AI và không trộn âm thanh. Bật thuyết minh nếu muốn lồng tiếng Việt và điều chỉnh tỷ lệ âm lượng gốc / nhạc nền.',
                      style: TextStyle(
                        fontSize: 12,
                        color: Color(0xFF78350F),
                        height: 1.38,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 11),
          Material(
            color: Colors.white,
            borderRadius: BorderRadius.circular(11),
            child: InkWell(
              onTap: () {
                HapticFeedback.selectionClick();
                _update((c) => c.copyWith(mode: 2));
              },
              borderRadius: BorderRadius.circular(11),
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 12,
                  vertical: 9,
                ),
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(11),
                  border: Border.all(color: AppColors.brandBorder),
                ),
                child: const Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(
                      Icons.auto_awesome_rounded,
                      size: 15,
                      color: AppColors.brand,
                    ),
                    SizedBox(width: 6),
                    Flexible(
                      child: Text(
                        'Chuyển sang Phụ đề + Thuyết minh AI',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 12.2,
                          fontWeight: FontWeight.w800,
                          color: AppColors.brandDark,
                        ),
                      ),
                    ),
                    SizedBox(width: 5),
                    Icon(
                      Icons.arrow_forward_rounded,
                      size: 15,
                      color: AppColors.brandDark,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  static String _langName(String code) {
    return switch (code.toLowerCase()) {
      'auto' => 'Tự động',
      'zh-cn' || 'zh' => 'Trung',
      'vi' => 'Việt',
      'en' => 'Anh',
      'ja' => 'Nhật',
      'ko' => 'Hàn',
      'th' => 'Thái',
      'id' => 'Indo',
      'es' => 'Tây Ban Nha',
      _ => code.toUpperCase(),
    };
  }

  Widget _quickSubmitBar(TranslateFormState state) {
    final config = state.config;
    final modeLabel = switch (config.mode) {
      1 => 'Phụ đề',
      2 => 'Phụ đề + Voice',
      4 => 'Chỉ tải video',
      _ => 'Voice + Nhạc',
    };
    final langLabel = config.downloadOnly
        ? 'Chỉ tải video gốc'
        : '${_langName(config.sourceLanguage)} → ${_langName(config.targetLanguage)}';

    return Material(
      color: Colors.transparent,
      child: Container(
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(999),
          boxShadow: const [
            BoxShadow(
              color: Color(0x220F172A),
              blurRadius: 28,
              spreadRadius: -4,
              offset: Offset(0, 12),
            ),
            BoxShadow(
              color: Color(0x1FEA580C),
              blurRadius: 12,
              offset: Offset(0, 4),
            ),
          ],
        ),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(999),
          child: BackdropFilter(
            filter: ImageFilter.blur(sigmaX: 20, sigmaY: 20),
            child: Container(
              padding: const EdgeInsets.fromLTRB(10, 6, 6, 6),
              decoration: BoxDecoration(
                gradient: const LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Color(0xF0FFFFFF), Color(0xF8FFFAF5)],
                ),
                borderRadius: BorderRadius.circular(999),
                border: Border.all(color: const Color(0xE6FED7AA), width: 1.5),
              ),
              child: Row(
                children: [
                  GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onTap: _submit,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 9,
                        vertical: 5,
                      ),
                      decoration: BoxDecoration(
                        gradient: const LinearGradient(
                          begin: Alignment.topCenter,
                          end: Alignment.bottomCenter,
                          colors: [Color(0xFFFFF7ED), Color(0xFFFFEDD5)],
                        ),
                        borderRadius: BorderRadius.circular(999),
                        border: Border.all(
                          color: const Color(0xFFFDBA74),
                          width: 1.4,
                        ),
                      ),
                      child: Text(
                        '${state.sourceCount}/50',
                        style: const TextStyle(
                          fontSize: 11.5,
                          fontWeight: FontWeight.w900,
                          color: AppColors.brandDark,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 9),
                  Expanded(
                    child: GestureDetector(
                      behavior: HitTestBehavior.opaque,
                      onTap: _submit,
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            modeLabel,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 12.5,
                              fontWeight: FontWeight.w800,
                              color: AppColors.ink,
                            ),
                          ),
                          Text(
                            langLabel,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 10.5,
                              fontWeight: FontWeight.w600,
                              color: AppColors.muted,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  // Mirrors web `.button.primary.mobile-quick-cta` (styles.css).
                  // Uses a dedicated gradient container with vibrant orange gradient
                  // so the CTA stands out crisp and bold on top of the floating bar.
                  Container(
                    decoration: BoxDecoration(
                      gradient: const LinearGradient(
                        // linear-gradient(135deg, #f97316 0%, #ea580c 100%)
                        colors: [Color(0xFFF97316), Color(0xFFEA580C)],
                        begin: Alignment.topLeft,
                        end: Alignment.bottomRight,
                      ),
                      borderRadius: BorderRadius.circular(999),
                      border: Border.all(
                        color: const Color(0x66FFFFFF),
                        width: 1.2,
                      ),
                      boxShadow: const [
                        BoxShadow(
                          color: Color(0x6BEA580C),
                          blurRadius: 16,
                          spreadRadius: -2,
                          offset: Offset(0, 5),
                        ),
                      ],
                    ),
                    child: Material(
                      color: Colors.transparent,
                      child: InkWell(
                        onTap: _submit,
                        borderRadius: BorderRadius.circular(999),
                        splashColor: const Color(0x33FFFFFF),
                        highlightColor: const Color(0x1AFFFFFF),
                        child: ConstrainedBox(
                          // min-height: 40px
                          constraints: const BoxConstraints(minHeight: 40),
                          child: Padding(
                            // padding: 6px 12px 6px 14px
                            padding: const EdgeInsets.fromLTRB(14, 6, 12, 6),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Text(
                                  // Same labels as syncMobileQuickBar() in web/js/app.js.
                                  config.downloadOnly
                                      ? (state.sourceCount > 0
                                            ? 'Tải ngay (${state.sourceCount})'
                                            : 'Tải ngay')
                                      : (state.sourceCount > 0
                                            ? 'Bắt đầu (${state.sourceCount})'
                                            : 'Bắt đầu'),
                                  maxLines: 1,
                                  style: const TextStyle(
                                    color: Colors.white,
                                    fontSize: 12.8, // 0.8rem
                                    fontWeight: FontWeight.w800,
                                    letterSpacing: -0.13, // -0.01em
                                    shadows: [
                                      // text-shadow: 0 1px 2px rgba(0, 0, 0, 0.15)
                                      Shadow(
                                        color: Color(0x26000000),
                                        offset: Offset(0, 1),
                                        blurRadius: 2,
                                      ),
                                    ],
                                  ),
                                ),
                                const SizedBox(width: 6), // gap: 6px
                                Container(
                                  width: 22,
                                  height: 22,
                                  alignment: Alignment.center,
                                  decoration: const BoxDecoration(
                                    color: Color(
                                      0x3DFFFFFF,
                                    ), // rgba(255, 255, 255, 0.24)
                                    shape: BoxShape.circle,
                                  ),
                                  child: const Text(
                                    '→',
                                    style: TextStyle(
                                      color: Colors.white,
                                      fontSize: 13.1, // 0.82rem
                                      fontWeight: FontWeight.w800,
                                      height: 1,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _submitCard(TranslateFormState state) {
    final config = state.config;
    final modeLabel = switch (config.mode) {
      1 => 'Phụ đề',
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
                  child: _SummaryItem(label: 'Chế độ', value: modeLabel),
                ),
                Container(width: 1, height: 26, color: AppColors.line),
                const SizedBox(width: 10),
                Expanded(
                  child: _SummaryItem(
                    label: 'Ngôn ngữ',
                    value: config.downloadOnly
                        ? 'Giữ nguyên'
                        : '${_langName(config.sourceLanguage)} → ${_langName(config.targetLanguage)}',
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
          StudioGradientButton(
            onPressed: _submit,
            icon: Icons.rocket_launch_rounded,
            label: state.sourceCount > 0
                ? (config.downloadOnly
                      ? 'Tải batch (${state.sourceCount} video)'
                      : 'Bắt đầu xử lý batch (${state.sourceCount} video)')
                : (config.downloadOnly
                      ? 'Bắt đầu tải video'
                      : 'Bắt đầu xử lý batch'),
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
                            color: selected
                                ? AppColors.brandDark
                                : AppColors.ink,
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
  const _SummaryItem({required this.label, required this.value});

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

class _LinkSourceTile extends StatelessWidget {
  const _LinkSourceTile({required this.url, required this.onRemove});

  final String url;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    final code = shortSocialPlatformCode(url);
    final badgeColor = switch (code) {
      'DY' => const Color(0xFF0F172A),
      'YT' => const Color(0xFFDC2626),
      'FB' => const Color(0xFF2563EB),
      _ => AppColors.tealDark,
    };

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(11),
        border: Border.all(color: AppColors.line),
      ),
      child: Row(
        children: [
          Container(
            width: 30,
            height: 26,
            decoration: BoxDecoration(
              color: badgeColor.withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(7),
              border: Border.all(color: badgeColor.withValues(alpha: 0.25)),
            ),
            alignment: Alignment.center,
            child: Text(
              code,
              style: TextStyle(
                fontSize: 10.5,
                fontWeight: FontWeight.w900,
                color: badgeColor,
              ),
            ),
          ),
          const SizedBox(width: 9),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  url,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 12.2,
                    fontWeight: FontWeight.w700,
                    color: AppColors.ink,
                  ),
                ),
                const Text(
                  'Sẽ tải tự động trong hàng đợi nền',
                  style: TextStyle(fontSize: 10.5, color: AppColors.muted),
                ),
              ],
            ),
          ),
          IconButton(
            visualDensity: VisualDensity.compact,
            tooltip: 'Xóa link này',
            icon: const Icon(Icons.close_rounded, size: 17),
            onPressed: onRemove,
          ),
        ],
      ),
    );
  }
}

/// Tells the user what the server handles on its own, so a photo post, a
/// blocked TikTok or a same-language video does not look like a mistake.
class _SmartSourceNotice extends StatefulWidget {
  const _SmartSourceNotice();

  @override
  State<_SmartSourceNotice> createState() => _SmartSourceNoticeState();
}

class _SmartSourceNoticeState extends State<_SmartSourceNotice> {
  bool _expanded = false;

  static const _items = <({IconData icon, String title, String body})>[
    (
      icon: Icons.photo_library_outlined,
      title: 'Bài đăng ảnh TikTok → tự dựng thành video',
      body:
          'Mỗi ảnh hiện 3 giây kèm nhạc nền của bài. Bài ảnh không có lời nói, '
          'nên hãy chọn phụ đề lấy từ chữ trên ảnh (OCR).',
    ),
    (
      icon: Icons.hd_outlined,
      title: 'Tự lấy bản HD, không logo TikTok',
      body:
          'Khi TikTok chặn tải trực tiếp, hệ thống tự chuyển nguồn dự phòng '
          'và ưu tiên bản gốc chất lượng cao nhất.',
    ),
    (
      icon: Icons.translate_rounded,
      title: 'Video đã đúng ngôn ngữ đích → không dịch',
      body:
          'Ví dụ video tiếng Việt, đích tiếng Việt: giữ nguyên lời gốc, '
          'chỉ tạo phụ đề hoặc lồng tiếng.',
    ),
  ];

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: AppColors.brandTint,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: AppColors.brandBorder),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          InkWell(
            borderRadius: BorderRadius.circular(14),
            onTap: () => setState(() => _expanded = !_expanded),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 10, 8, 10),
              child: Row(
                children: [
                  const Icon(
                    Icons.auto_awesome_rounded,
                    size: 18,
                    color: AppColors.brand,
                  ),
                  const SizedBox(width: 8),
                  const Expanded(
                    child: Text(
                      'Hệ thống tự xử lý thông minh',
                      style: TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w800,
                        color: AppColors.brandDark,
                      ),
                    ),
                  ),
                  Text(
                    _expanded ? 'Thu gọn' : 'Xem',
                    style: const TextStyle(
                      fontSize: 11.5,
                      fontWeight: FontWeight.w700,
                      color: AppColors.brand,
                    ),
                  ),
                  Icon(
                    _expanded
                        ? Icons.expand_less_rounded
                        : Icons.expand_more_rounded,
                    size: 18,
                    color: AppColors.brand,
                  ),
                ],
              ),
            ),
          ),
          if (_expanded)
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
              child: Column(
                children: [
                  for (final item in _items) ...[
                    const SizedBox(height: 8),
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(item.icon, size: 17, color: AppColors.brand),
                        const SizedBox(width: 9),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                item.title,
                                style: const TextStyle(
                                  fontSize: 12,
                                  fontWeight: FontWeight.w800,
                                  color: AppColors.ink,
                                ),
                              ),
                              const SizedBox(height: 2),
                              Text(
                                item.body,
                                style: const TextStyle(
                                  fontSize: 11.5,
                                  height: 1.35,
                                  color: AppColors.inkSecondary,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ],
                ],
              ),
            ),
        ],
      ),
    );
  }
}
