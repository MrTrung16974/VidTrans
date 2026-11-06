import 'dart:io';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/account.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../../home/presentation/screens/home_shell.dart';
import '../../../profile/presentation/screens/billing_screen.dart';
import '../../../screen_translator/screen_translator_controller.dart';
import '../../data/block_translators.dart';
import '../../domain/image_languages.dart';
import '../../domain/image_translation_pipeline.dart';
import '../providers/image_translation_providers.dart';
import '../widgets/translated_image.dart';

const _stageLabels = {
  TranslationStage.reading: 'Đang đọc chữ trong ảnh…',
  TranslationStage.detecting: 'Đang nhận diện ngôn ngữ…',
  TranslationStage.translating: 'Đang dịch…',
  TranslationStage.layout: 'Đang căn chữ vào ảnh…',
};

const _prefsKey = 'image_translation.settings';

/// "Dịch ảnh & Dịch nổi": pick a screenshot or activate the live floating
/// screen translator overlay.
class ImageTranslateScreen extends ConsumerStatefulWidget {
  const ImageTranslateScreen({super.key, this.initialLiveMode = false});

  final bool initialLiveMode;

  @override
  ConsumerState<ImageTranslateScreen> createState() =>
      _ImageTranslateScreenState();
}

class _ImageTranslateScreenState extends ConsumerState<ImageTranslateScreen> {
  String? _path;
  Uint8List? _bytes;
  String _source = autoLanguage;
  String _target = 'vi';
  // Online by default: the server translators read far better than the
  // on-device models; Offline stays one tap away for no network or privacy.
  TranslationMode _mode = TranslationMode.online;

  /// Online only: the server's AI translator ("Chất lượng") instead of the
  /// fast one ("Nhanh", the default; a live screen needs the speed).
  bool _aiQuality = false;

  /// What the floating translator services receive as `mode`.
  String get _wireMode => _mode == TranslationMode.offline
      ? TranslationMode.offline.name
      : (_aiQuality ? onlineQualityMode : TranslationMode.online.name);

  TranslationStage? _stage;
  ImageTranslationResult? _result;
  int? _selectedRegionId;
  bool _showOriginal = false;
  bool _sharing = false;
  String? _error;

  /// Floating "Dịch" button over other apps (phase 2, Android).
  bool _floating = false;
  bool _floatingBusy = false;

  /// Live (phase 3): translate again by itself whenever the screen settles.
  bool _live = false;
  late bool _liveTab = widget.initialLiveMode;

  /// Accessibility mode is off: the service is removed from the manifest so
  /// Play Protect doesn't block sideloaded installs. See AndroidManifest.xml.
  static const kA11yModeAvailable = false;

  /// Accessibility mode instead of screen capture: text read from the app's
  /// view tree, opaque translations, no capture consent each session.
  bool _a11y = false;

  /// The user switched the VidTrans service on in Settings › Accessibility.
  bool _a11yGranted = false;

  /// Turn on as soon as the user comes back from the accessibility settings.
  bool _a11yPending = false;
  late final AppLifecycleListener _lifecycle;

  bool get _busy => _stage != null;

  ScreenTranslatorController get _floatingController =>
      ref.read(screenTranslatorControllerProvider);

  @override
  void initState() {
    super.initState();
    _restoreSettings();
    // The user may stop the button from the notification or come back from
    // the overlay permission screen.
    _lifecycle = AppLifecycleListener(onResume: _refreshFloating);
    _refreshFloating();
  }

  @override
  void didUpdateWidget(covariant ImageTranslateScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.initialLiveMode != oldWidget.initialLiveMode) {
      setState(() => _liveTab = widget.initialLiveMode);
    }
  }

  @override
  void dispose() {
    _lifecycle.dispose();
    _result?.dispose();
    super.dispose();
  }

  Future<void> _refreshFloating() async {
    if (!_floatingController.supported) return;
    try {
      final controller = _floatingController;
      final running = await controller.isRunning();
      final live = running && await controller.isLive();
      final granted =
          kA11yModeAvailable && await controller.a11yEnabledInSettings();
      var a11yOn = granted && await controller.a11yTranslating();
      if (granted && _a11yPending && !a11yOn) {
        await _startA11y();
        a11yOn = true;
      }
      _a11yPending = false;
      if (!mounted) return;
      setState(() {
        _a11yGranted = granted;
        if (a11yOn) _a11y = true;
        _floating = _a11y ? a11yOn : running;
        if (running) _live = live;
      });
    } catch (_) {}
  }

  /// Saves the choice and hands it to a running floating button.
  void _settingsChanged() {
    _saveSettings();
    if (_floating && _a11y) {
      _floatingController
          .setA11yConfig(source: _source, target: _target, mode: _wireMode)
          .catchError((_) {});
    } else if (_floating) {
      _floatingController
          .updateSettings(
            source: _source,
            target: _target,
            mode: _wireMode,
            live: _live,
          )
          .catchError((_) {});
    }
  }

  Future<void> _toggleFloating() async {
    final controller = _floatingController;
    if (_floatingBusy) return;
    setState(() => _floatingBusy = true);
    try {
      if (_floating) {
        if (_a11y) {
          await controller.setA11yConfig(
            enabled: false,
            source: _source,
            target: _target,
            mode: _wireMode,
          );
        } else {
          await controller.stop();
        }
        if (mounted) setState(() => _floating = false);
        return;
      }
      if (_source != autoLanguage && _source == _target) {
        setState(
          () => _error = 'Ngôn ngữ nguồn và ngôn ngữ đích phải khác nhau.',
        );
        return;
      }
      if (_mode == TranslationMode.offline) {
        // The service cannot ask, so download the models now.
        final needed = {_source == autoLanguage ? 'zh-CN' : _source, _target};
        final missing = await ref
            .read(offlineTranslatorProvider)
            .missingModels(needed);
        if (missing.isNotEmpty && !await _askDownload(missing)) return;
      }
      if (_a11y) {
        await _toggleA11yOn();
        return;
      }
      if (!await controller.canDrawOverlays()) {
        if (!mounted) return;
        final open = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('Cho phép hiển thị trên ứng dụng khác'),
            content: const Text(
              'Nút “Dịch” cần nổi trên WeChat, Zalo, trình duyệt… Hãy bật '
              '“Hiển thị trên các ứng dụng khác” cho VidTrans, rồi quay lại bấm Bật.',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('Để sau'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('Mở cài đặt'),
              ),
            ],
          ),
        );
        if (open == true) await controller.openOverlaySettings();
        return;
      }
      _saveSettings();
      final started = await controller.start(
        source: _source,
        target: _target,
        mode: _wireMode,
        live: _live,
      );
      if (!mounted) return;
      setState(() => _floating = started);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            started
                ? (_live
                      ? 'Đã bật dịch liên tục. Mở ứng dụng bất kỳ: bản dịch tự hiện khi màn hình đứng yên.'
                      : 'Đã bật nút “Dịch”. Mở ứng dụng bất kỳ và chạm nút để dịch màn hình.')
                : 'Chưa bật: cần cho phép chia sẻ toàn màn hình.',
          ),
        ),
      );
    } catch (error) {
      if (mounted) setState(() => _error = errorMessage(error));
    } finally {
      if (mounted) setState(() => _floatingBusy = false);
    }
  }

  /// Accessibility mode: needs the service switched on in the system
  /// settings once (after a clear explanation of what it reads), then turns
  /// on and off from here without asking again.
  Future<void> _toggleA11yOn() async {
    final controller = _floatingController;
    if (!await controller.a11yEnabledInSettings()) {
      if (!mounted) return;
      final open = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Bật Dịch vụ Trợ năng của VidTrans'),
          content: const SingleChildScrollView(
            child: Text(
              'Ở chế độ Trợ năng, VidTrans đọc chữ đang hiển thị của ứng dụng bạn mở '
              '(không đọc ô nhập liệu hay mật khẩu) để dịch và che kín chữ gốc bằng bản dịch, '
              'đúng vị trí, cuộn theo ngay.\n\n'
              '• Chế độ Online: chữ được gửi tới máy chủ VidTrans để dịch, không lưu lại.\n'
              '• Chế độ Offline: dịch hoàn toàn trên máy.\n'
              '• Ảnh chụp màn hình chỉ dùng để lấy màu nền, không lưu, không gửi đi.\n\n'
              'Trong Cài đặt › Hỗ trợ tiếp cận, chọn “VidTrans – Dịch màn hình” và bật lên, '
              'rồi quay lại ứng dụng. Bạn có thể tắt bất cứ lúc nào.',
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Không đồng ý'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Đồng ý, mở cài đặt'),
            ),
          ],
        ),
      );
      if (open == true) {
        _a11yPending = true;
        await controller.openAccessibilitySettings();
      }
      return;
    }
    await _startA11y();
    if (!mounted) return;
    setState(() => _floating = true);
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text(
          'Đã bật dịch Trợ năng. Mở ứng dụng bất kỳ: chữ được dịch và che kín tại chỗ. '
          'Chạm nút nổi để tạm dừng, nhấn giữ để dịch lại.',
        ),
      ),
    );
  }

  Future<void> _startA11y() async {
    final controller = _floatingController;
    // One translator at a time: both would draw over the same text.
    if (await controller.isRunning()) await controller.stop();
    _saveSettings();
    await controller.setA11yConfig(
      enabled: true,
      source: _source,
      target: _target,
      mode: _wireMode,
    );
  }

  Future<void> _setA11yMode(bool a11y) async {
    if (a11y == _a11y || _floatingBusy) return;
    // Switching mode switches the running translator off.
    if (_floating) await _toggleFloating();
    if (!mounted) return;
    setState(() => _a11y = a11y);
    _saveSettings();
  }

  Future<void> _restoreSettings() async {
    try {
      final saved = (await SharedPreferences.getInstance()).getStringList(
        _prefsKey,
      );
      if (saved == null || saved.length < 3 || !mounted) return;
      setState(() {
        if (saved[0] == autoLanguage || imageLanguage(saved[0]) != null) {
          _source = saved[0];
        }
        if (imageTargetCodes.contains(saved[1])) _target = saved[1];
        _mode = TranslationMode.values.firstWhere(
          (m) => m.name == saved[2],
          orElse: () => _mode,
        );
        if (saved.length > 3) _live = saved[3] == 'live';
        if (saved.length > 4) {
          _a11y = kA11yModeAvailable && saved[4] == 'a11y';
        }
        if (saved.length > 5) _aiQuality = saved[5] == 'quality';
      });
    } catch (_) {
      // Preferences are a convenience; defaults are fine.
    }
  }

  Future<void> _saveSettings() async {
    try {
      await (await SharedPreferences.getInstance()).setStringList(_prefsKey, [
        _source,
        _target,
        _mode.name,
        if (_live) 'live' else 'tap',
        if (_a11y) 'a11y' else 'capture',
        if (_aiQuality) 'quality' else 'fast',
      ]);
    } catch (_) {}
  }

  void _setResult(ImageTranslationResult? result) {
    final previous = _result;
    _result = result;
    _selectedRegionId = null;
    if (previous != null && !identical(previous.image, result?.image)) {
      previous.dispose();
    }
  }

  void _updateRegionTranslation(int regionId, String text) {
    final current = _result;
    if (current == null) return;
    final updatedRegions = [
      for (final r in current.regions)
        if (r.id == regionId) r.copyWith(translation: text) else r,
    ];
    setState(() {
      _result = ImageTranslationResult(
        image: current.image,
        regions: updatedRegions,
      );
    });
  }

  Future<void> _pick() async {
    final files = await FilePicker.pickFiles(type: FileType.image);
    if (files.isEmpty) return;
    final path = files.first.path ?? files.first.xFile.path;
    final bytes = await File(path).readAsBytes();
    if (!mounted) return;
    setState(() {
      _path = path;
      _bytes = bytes;
      _error = null;
      _showOriginal = false;
      _setResult(null);
    });
    await _translate();
  }

  Future<void> _translate() async {
    final path = _path, bytes = _bytes;
    if (path == null || bytes == null || _busy) return;
    if (_source != autoLanguage && _source == _target) {
      setState(
        () => _error = 'Ngôn ngữ nguồn và ngôn ngữ đích phải khác nhau.',
      );
      return;
    }
    _saveSettings();
    setState(() {
      _error = null;
      _stage = TranslationStage.reading;
    });
    final BlockTranslator translator = _mode == TranslationMode.offline
        ? ref.read(offlineTranslatorProvider)
        : _aiQuality
        ? ref.read(onlineTranslatorProvider)
        : ref.read(fastOnlineTranslatorProvider);
    try {
      final result = await ref
          .read(imageTranslationPipelineProvider)
          .run(
            imagePath: path,
            bytes: bytes,
            source: _source,
            target: _target,
            translate: translator.translate,
            onStage: (stage) {
              if (mounted) setState(() => _stage = stage);
            },
          );
      if (!mounted) {
        result.dispose();
        return;
      }
      setState(() {
        _setResult(result);
        _showOriginal = false;
        if (result.regions.isEmpty) {
          _error =
              'Không tìm thấy chữ trong ảnh. Hãy thử ảnh rõ nét hơn hoặc chọn đúng ngôn ngữ nguồn.';
        } else if (result.translatedCount == 0) {
          _error =
              'Chữ trong ảnh đã ở ngôn ngữ đích hoặc không có đoạn nào cần dịch.';
        }
      });
    } on ModelsMissing catch (missing) {
      if (mounted) setState(() => _stage = null);
      if (await _askDownload(missing.languages)) await _translate();
      return;
    } catch (error) {
      if (mounted) setState(() => _error = errorMessage(error));
    } finally {
      if (mounted) setState(() => _stage = null);
    }
  }

  /// Offline models are ~30 MB each: ask first, Wi-Fi only unless allowed.
  Future<bool> _askDownload(List<String> languages) async {
    final names = languages
        .map((code) => imageLanguage(code)?.label ?? code)
        .join(', ');
    final choice = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Tải gói dịch offline'),
        content: Text(
          'Cần tải gói ngôn ngữ $names (khoảng 30 MB mỗi gói) một lần để dịch không cần mạng. '
          'Hoặc chuyển sang chế độ Online để dịch qua máy chủ VidTrans.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Hủy'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, 'any'),
            child: const Text('Tải bằng dữ liệu di động'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, 'wifi'),
            child: const Text('Tải qua Wi-Fi'),
          ),
        ],
      ),
    );
    if (choice == null || !mounted) return false;
    setState(() => _stage = TranslationStage.translating);
    try {
      await ref
          .read(offlineTranslatorProvider)
          .download(languages, wifiOnly: choice == 'wifi');
      return true;
    } catch (error) {
      if (mounted) {
        setState(
          () => _error = choice == 'wifi'
              ? 'Chưa tải được gói ngôn ngữ. Hãy bật Wi-Fi hoặc chọn tải bằng dữ liệu di động.'
              : errorMessage(error),
        );
      }
      return false;
    } finally {
      if (mounted) setState(() => _stage = null);
    }
  }

  Future<void> _share() async {
    final result = _result;
    if (result == null || _sharing) return;
    setState(() => _sharing = true);
    try {
      final png = await renderTranslatedPng(result);
      final dir = await getTemporaryDirectory();
      final file = File(
        '${dir.path}/vidtrans_dich_${DateTime.now().millisecondsSinceEpoch}.png',
      );
      await file.writeAsBytes(png, flush: true);
      await SharePlus.instance.share(
        ShareParams(files: [XFile(file.path, mimeType: 'image/png')]),
      );
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(errorMessage(error))));
      }
    } finally {
      if (mounted) setState(() => _sharing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final account = ref.watch(sessionProvider).valueOrNull;
    final allowed = account?.hasFeature(
          _liveTab ? PlanFeature.screenTranslator : PlanFeature.imageTranslation,
        ) ??
        false;
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: StudioTopBar(
        activeStudioPill: _liveTab
            ? StudioCategoryPill.live
            : StudioCategoryPill.image,
        onSelectStudioPill: (pill) {
          switch (pill) {
            case StudioCategoryPill.video:
              context.go('/translate');
            case StudioCategoryPill.image:
              setState(() => _liveTab = false);
              context.go('/translate/image');
            case StudioCategoryPill.live:
              setState(() => _liveTab = true);
              context.go('/translate/image?mode=live');
          }
        },
      ),
      body: allowed ? _body(account!) : _locked(),
    );
  }

  Widget _locked() => Padding(
    padding: EdgeInsets.only(bottom: studioDockClearance(context)),
    child: StudioEmptyState(
      icon: Icons.lock_outline_rounded,
      title: 'Gói của bạn chưa có tính năng Dịch ảnh',
      subtitle: 'Liên hệ quản trị viên hoặc nâng cấp gói để sử dụng.',
      action: FilledButton(
        onPressed: () => Navigator.of(
          context,
        ).push(MaterialPageRoute<void>(builder: (_) => const BillingScreen())),
        child: const Text('Xem gói dịch vụ'),
      ),
    ),
  );

  Widget _heroBanner() {
    final isLive = _liveTab;
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        gradient: AppColors.studioHeroGradient,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.brandBorder),
        boxShadow: AppColors.cardShadow,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
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
                      isLive
                          ? Icons.bolt_rounded
                          : Icons.document_scanner_rounded,
                      size: 13,
                      color: AppColors.brandDark,
                    ),
                    const SizedBox(width: 4),
                    Text(
                      isLive ? 'DỊCH MÀN HÌNH LIVE' : 'AI OCR & DỊCH ẢNH',
                      style: const TextStyle(
                        fontSize: 10.5,
                        fontWeight: FontWeight.w800,
                        color: AppColors.brandDark,
                        letterSpacing: 0.4,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Flexible(
                child: Text(
                  isLive ? 'Nút nổi đa ứng dụng' : 'Giữ nguyên bố cục ảnh',
                  style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: AppColors.muted,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            isLive
                ? 'Nút dịch nổi trên mọi ứng dụng'
                : 'Dịch ảnh chụp màn hình tức thì',
            style: const TextStyle(
              fontSize: 19,
              fontWeight: FontWeight.w900,
              color: AppColors.ink,
              letterSpacing: -0.4,
              height: 1.2,
            ),
          ),
          const SizedBox(height: 5),
          Text(
            isLive
                ? 'Bật nút “Dịch” nổi trên WeChat, Zalo, Telegram, trình duyệt hoặc truyện để dịch trực tiếp không cần chụp màn hình.'
                : 'Quét chữ Trung, Anh, Nhật, Hàn… trong ảnh chụp màn hình và thay bằng tiếng Việt ngay tại vị trí chữ gốc.',
            style: const TextStyle(
              fontSize: 13,
              color: AppColors.muted,
              height: 1.4,
            ),
          ),
        ],
      ),
    );
  }

  Widget _body(Account account) {
    final result = _result;
    return ListView(
      padding: EdgeInsets.fromLTRB(
        14,
        14,
        14,
        studioDockBottomPadding(context, extra: 40),
      ),
      children: [
        _heroBanner(),
        const SizedBox(height: 14),
        if (_liveTab) ...[
          if (_floatingController.supported) ...[
            _floatingCard(),
            const SizedBox(height: 14),
          ] else ...[
            _unsupportedLiveCard(),
            const SizedBox(height: 14),
          ],
          _settingsCard(account),
          const SizedBox(height: 14),
          if (_bytes == null) _pickCard() else _imageCard(result),
        ] else ...[
          _settingsCard(account),
          const SizedBox(height: 14),
          if (_bytes == null) _pickCard() else _imageCard(result),
        ],
        if (_busy) ...[const SizedBox(height: 14), _progress()],
        if (_error != null) ...[const SizedBox(height: 14), _errorBox(_error!)],
        if (result != null && result.translatedCount > 0) ...[
          const SizedBox(height: 14),
          _regionsCard(result),
        ],
      ],
    );
  }

  Widget _settingsCard(Account account) {
    final sourceItems = [
      const DropdownMenuItem(value: autoLanguage, child: Text('Tự nhận diện')),
      for (final language in imageLanguages)
        DropdownMenuItem(value: language.code, child: Text(language.label)),
    ];
    final targetItems = [
      for (final code in imageTargetCodes)
        DropdownMenuItem(
          value: code,
          child: Text(imageLanguage(code)?.label ?? code),
        ),
    ];
    final canSwap =
        !_busy && _source != autoLanguage && imageTargetCodes.contains(_source);
    return StudioCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: DropdownButtonFormField<String>(
                  key: ValueKey('img_src_$_source'),
                  initialValue: _source,
                  isExpanded: true,
                  decoration: const InputDecoration(labelText: 'Từ'),
                  items: sourceItems,
                  onChanged: _busy
                      ? null
                      : (value) {
                          setState(() => _source = value ?? _source);
                          _settingsChanged();
                        },
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 4),
                child: IconButton(
                  tooltip: canSwap
                      ? 'Đảo chiều ngôn ngữ'
                      : 'Chọn ngôn ngữ nguồn cụ thể để đảo chiều',
                  visualDensity: VisualDensity.compact,
                  onPressed: canSwap
                      ? () {
                          setState(() {
                            final prevSource = _source;
                            _source = _target;
                            _target = prevSource;
                          });
                          _settingsChanged();
                        }
                      : null,
                  icon: Icon(
                    Icons.swap_horiz_rounded,
                    color: canSwap ? AppColors.brandDark : AppColors.muted,
                    size: 22,
                  ),
                ),
              ),
              Expanded(
                child: DropdownButtonFormField<String>(
                  key: ValueKey('img_tgt_$_target'),
                  initialValue: _target,
                  isExpanded: true,
                  decoration: const InputDecoration(labelText: 'Sang'),
                  items: targetItems,
                  onChanged: _busy
                      ? null
                      : (value) {
                          setState(() => _target = value ?? _target);
                          _settingsChanged();
                        },
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          SegmentedButton<TranslationMode>(
            segments: const [
              ButtonSegment(
                value: TranslationMode.online,
                icon: Icon(Icons.cloud_outlined, size: 18),
                label: Text('Online'),
              ),
              ButtonSegment(
                value: TranslationMode.offline,
                icon: Icon(Icons.phone_android_rounded, size: 18),
                label: Text('Offline'),
              ),
            ],
            selected: {_mode},
            onSelectionChanged: _busy
                ? null
                : (value) {
                    setState(() => _mode = value.first);
                    _settingsChanged();
                  },
          ),
          if (_mode == TranslationMode.online) ...[
            const SizedBox(height: 10),
            SegmentedButton<bool>(
              segments: const [
                ButtonSegment(
                  value: false,
                  icon: Icon(Icons.bolt_rounded, size: 18),
                  label: Text('Nhanh'),
                ),
                ButtonSegment(
                  value: true,
                  icon: Icon(Icons.auto_awesome_rounded, size: 18),
                  label: Text('Chất lượng AI'),
                ),
              ],
              selected: {_aiQuality},
              onSelectionChanged: _busy
                  ? null
                  : (value) {
                      setState(() => _aiQuality = value.first);
                      _settingsChanged();
                    },
            ),
          ],
          const SizedBox(height: 8),
          Text(
            _mode == TranslationMode.offline
                ? 'Đọc chữ và dịch ngay trên điện thoại. Ảnh và nội dung không rời khỏi máy.'
                : _aiQuality
                ? 'Dịch bằng AI của VidTrans: câu tự nhiên, nhận ra tên riêng, nhưng chậm hơn khoảng 4 lần. '
                      'Chỉ gửi phần chữ lên máy chủ, ảnh không được tải lên.'
                : 'Dịch nhanh qua máy chủ VidTrans, hợp để đọc truyện, chat liên tục. '
                      'Chỉ gửi phần chữ lên máy chủ, ảnh không được tải lên.',
            style: const TextStyle(
              fontSize: 12.5,
              color: AppColors.muted,
              height: 1.4,
            ),
          ),
        ],
      ),
    );
  }

  Widget _unsupportedLiveCard() => StudioCard(
    borderColor: AppColors.amberBorder,
    backgroundColor: AppColors.amberSoft,
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
                color: Colors.white,
                borderRadius: BorderRadius.circular(11),
                border: Border.all(color: AppColors.amberBorder),
              ),
              alignment: Alignment.center,
              child: const Icon(
                Icons.android_rounded,
                color: AppColors.amberDark,
                size: 21,
              ),
            ),
            const SizedBox(width: 12),
            const Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Dịch nổi trực tiếp chỉ hỗ trợ trên Android',
                    style: TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w800,
                      color: AppColors.ink,
                    ),
                  ),
                  SizedBox(height: 3),
                  Text(
                    'Tính năng nút nổi & đọc màn hình đa ứng dụng (MediaProjection overlay) là đặc quyền hệ điều hành Android. Trên thiết bị hiện tại, bạn hãy dùng chế độ Dịch ảnh chụp màn hình bên dưới.',
                    style: TextStyle(
                      fontSize: 12.2,
                      color: AppColors.amberText,
                      height: 1.38,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        FilledButton.tonalIcon(
          onPressed: () => setState(() => _liveTab = false),
          icon: const Icon(Icons.image_search_rounded, size: 18),
          label: const Text('Chuyển sang Dịch Ảnh Chụp Màn Hình'),
        ),
      ],
    ),
  );

  Widget _floatingCard() => StudioCard(
    borderColor: _floating ? AppColors.brand : AppColors.line,
    backgroundColor: _floating ? AppColors.brandTint : AppColors.surface,
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                color: AppColors.brand,
                shape: BoxShape.circle,
                border: Border.all(color: Colors.white, width: 2),
              ),
              alignment: Alignment.center,
              child: const Text(
                'Dịch',
                style: TextStyle(
                  color: Colors.white,
                  fontWeight: FontWeight.w800,
                  fontSize: 11.5,
                ),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Nút dịch nổi trên mọi ứng dụng',
                    style: TextStyle(
                      fontSize: 14.5,
                      fontWeight: FontWeight.w800,
                      color: AppColors.ink,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    _floating
                        ? (_a11y
                              ? 'Đang bật — chữ trên WeChat, Zalo, web… được dịch và che kín tại chỗ.'
                              : 'Đang bật — chạm nút “Dịch” trên WeChat, Zalo, web… để dịch màn hình.')
                        : 'Dịch ngay trên WeChat, Zalo, Telegram, web, truyện… không cần chụp màn hình.',
                    style: const TextStyle(
                      fontSize: 12.5,
                      color: AppColors.muted,
                      height: 1.35,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
        if (kA11yModeAvailable) ...[
          const SizedBox(height: 12),
          SegmentedButton<bool>(
            segments: const [
              ButtonSegment(
                value: false,
                icon: Icon(Icons.screenshot_monitor_rounded, size: 18),
                label: Text('Chụp màn hình'),
              ),
              ButtonSegment(
                value: true,
                icon: Icon(Icons.accessibility_new_rounded, size: 18),
                label: Text('Trợ năng'),
              ),
            ],
            selected: {_a11y},
            showSelectedIcon: false,
            onSelectionChanged: _floatingBusy
                ? null
                : (selection) => _setA11yMode(selection.first),
          ),
        ],
        const SizedBox(height: 12),
        _floating
            ? OutlinedButton.icon(
                onPressed: _floatingBusy ? null : _toggleFloating,
                icon: const Icon(Icons.stop_circle_outlined, size: 18),
                label: Text(_a11y ? 'Tắt dịch Trợ năng' : 'Tắt nút dịch nổi'),
              )
            : FilledButton.icon(
                onPressed: _floatingBusy || _busy ? null : _toggleFloating,
                icon: Icon(
                  _a11y && !_a11yGranted
                      ? Icons.settings_accessibility_rounded
                      : Icons.touch_app_rounded,
                  size: 18,
                ),
                label: Text(
                  _floatingBusy
                      ? 'Đang bật…'
                      : !_a11y
                      ? 'Bật nút dịch nổi'
                      : _a11yGranted
                      ? 'Bật dịch Trợ năng'
                      : 'Bật trong Cài đặt Trợ năng',
                ),
              ),
        if (_a11y) ...[
          const SizedBox(height: 10),
          const Text(
            'Đọc chữ trực tiếp từ ứng dụng (không chụp, không OCR): dịch chính xác, che kín chữ gốc, '
            'cuộn tới đâu bản dịch theo tới đó và vẫn chạm, cuộn app bên dưới bình thường. '
            'Không dịch được chữ nằm trong ảnh, video, game — dùng “Chụp màn hình” cho các trường hợp đó. '
            'Chạm nút nổi để tạm dừng, nhấn giữ để dịch lại.',
            style: TextStyle(
              fontSize: 11.5,
              color: AppColors.muted,
              height: 1.4,
            ),
          ),
        ] else ...[
          SwitchListTile.adaptive(
            contentPadding: EdgeInsets.zero,
            value: _live,
            onChanged: _floatingBusy
                ? null
                : (value) {
                    setState(() => _live = value);
                    _settingsChanged();
                  },
            title: const Text(
              'Tự dịch liên tục (Live)',
              style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700),
            ),
            subtitle: const Text(
              'Cuộn hay đổi màn hình là tự dịch lại khi màn hình đứng yên; vẫn chạm, cuộn app bên dưới được. '
              'Lớp dịch hơi trong suốt (Android yêu cầu). Nhấn giữ nút nổi để bật/tắt nhanh. Tốn pin hơn.',
              style: TextStyle(fontSize: 11.5, height: 1.35),
            ),
          ),
          const SizedBox(height: 6),
          const Text(
            'Mỗi lần bật, Android hỏi quyền chia sẻ màn hình. VidTrans chỉ chụp một khung khi bạn chạm nút, '
            'không quay video và không lưu ảnh. Chạm vào bản dịch để đóng.',
            style: TextStyle(
              fontSize: 11.5,
              color: AppColors.muted,
              height: 1.4,
            ),
          ),
        ],
      ],
    ),
  );

  Widget _pickCard() => StudioCard(
    padding: const EdgeInsets.fromLTRB(18, 26, 18, 22),
    child: Column(
      children: [
        Container(
          width: 58,
          height: 58,
          decoration: BoxDecoration(
            color: AppColors.brandSoft,
            borderRadius: BorderRadius.circular(16),
          ),
          child: const Icon(
            Icons.translate_rounded,
            color: AppColors.brand,
            size: 30,
          ),
        ),
        const SizedBox(height: 12),
        const Text(
          'Chọn ảnh chụp màn hình',
          style: TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w800,
            color: AppColors.ink,
          ),
        ),
        const SizedBox(height: 6),
        const Text(
          'Tin nhắn WeChat, Zalo, trang web, truyện… Chữ gốc sẽ được thay bằng bản dịch ngay trên ảnh.',
          textAlign: TextAlign.center,
          style: TextStyle(fontSize: 13, color: AppColors.muted, height: 1.4),
        ),
        const SizedBox(height: 16),
        StudioGradientButton(
          onPressed: _pick,
          icon: Icons.photo_library_outlined,
          label: 'Chọn ảnh',
        ),
      ],
    ),
  );

  Widget _imageCard(ImageTranslationResult? result) {
    final hasTranslation = result != null && result.translatedCount > 0;
    return StudioCard(
      padding: const EdgeInsets.all(10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (hasTranslation)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: SegmentedButton<bool>(
                segments: const [
                  ButtonSegment(value: false, label: Text('Bản dịch')),
                  ButtonSegment(value: true, label: Text('Bản gốc')),
                ],
                selected: {_showOriginal},
                onSelectionChanged: (value) =>
                    setState(() => _showOriginal = value.first),
              ),
            ),
          ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: ConstrainedBox(
              constraints: BoxConstraints(
                maxHeight: MediaQuery.sizeOf(context).height * 0.7,
              ),
              child: InteractiveViewer(
                maxScale: 5,
                child: result != null
                    ? GestureDetector(
                        // Hold to peek at the original, release to go back.
                        onLongPressStart: (_) =>
                            setState(() => _showOriginal = true),
                        onLongPressEnd: (_) =>
                            setState(() => _showOriginal = false),
                        child: TranslatedImageView(
                          result: result,
                          showOriginal: _showOriginal,
                          selectedRegionId: _selectedRegionId,
                          onSelectRegion: (id) => setState(
                            () => _selectedRegionId =
                                _selectedRegionId == id ? null : id,
                          ),
                        ),
                      )
                    : Image.memory(_bytes!, fit: BoxFit.contain),
              ),
            ),
          ),
          if (hasTranslation)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Text(
                'Chạm vào khung chữ để chọn & sửa · Giữ ngón tay để xem ảnh gốc',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 12, color: AppColors.muted),
              ),
            ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _busy ? null : _pick,
                  icon: const Icon(Icons.photo_library_outlined, size: 18),
                  label: const Text('Ảnh khác'),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: hasTranslation
                    ? FilledButton.icon(
                        onPressed: _sharing ? null : _share,
                        icon: const Icon(Icons.ios_share_rounded, size: 18),
                        label: Text(_sharing ? 'Đang xuất…' : 'Chia sẻ ảnh'),
                      )
                    : FilledButton.icon(
                        onPressed: _busy ? null : _translate,
                        icon: const Icon(Icons.translate_rounded, size: 18),
                        label: const Text('Dịch lại'),
                      ),
              ),
            ],
          ),
          if (hasTranslation)
            Align(
              child: TextButton(
                onPressed: _busy ? null : _translate,
                child: const Text('Dịch lại với cài đặt mới'),
              ),
            ),
        ],
      ),
    );
  }

  Widget _progress() => StudioCard(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          _stageLabels[_stage] ?? 'Đang xử lý…',
          style: const TextStyle(
            fontWeight: FontWeight.w700,
            color: AppColors.ink,
          ),
        ),
        const SizedBox(height: 10),
        const LinearProgressIndicator(minHeight: 6),
      ],
    ),
  );

  Widget _errorBox(String message) => Container(
    padding: const EdgeInsets.all(14),
    decoration: BoxDecoration(
      color: AppColors.amberSoft,
      borderRadius: BorderRadius.circular(14),
      border: Border.all(color: AppColors.amberBorder),
    ),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Icon(
          Icons.info_outline_rounded,
          color: AppColors.amberDark,
          size: 20,
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Text(
            message,
            style: const TextStyle(
              color: AppColors.amberText,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
      ],
    ),
  );

  /// Every translated block as text, so nothing is lost when a translation
  /// had to shrink or grow to fit (design: fallback instead of a wrong cover).
  Widget _regionsCard(ImageTranslationResult result) {
    final translated = result.regions
        .where((region) => region.translated)
        .toList();
    return StudioCard(
      padding: const EdgeInsets.fromLTRB(14, 6, 14, 6),
      child: Theme(
        data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          key: ValueKey('regions_${_selectedRegionId != null}'),
          initiallyExpanded: _selectedRegionId != null,
          tilePadding: EdgeInsets.zero,
          title: Text(
            'Đã dịch ${translated.length} đoạn',
            style: const TextStyle(
              fontWeight: FontWeight.w800,
              color: AppColors.ink,
            ),
          ),
          subtitle: Text(
            result.overflowCount > 0
                ? '${result.overflowCount} đoạn dài hơn chữ gốc nên được nới rộng khung · chạm để sửa'
                : 'Chạm vào số thứ tự trên ảnh hoặc bên dưới để đối chiếu và chỉnh sửa',
            style: const TextStyle(fontSize: 12, color: AppColors.muted),
          ),
          children: [
            for (final (index, region) in translated.indexed)
              Builder(
                builder: (context) {
                  final selected = _selectedRegionId == region.id;
                  return GestureDetector(
                    onTap: () => setState(
                      () => _selectedRegionId = selected ? null : region.id,
                    ),
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 160),
                      margin: const EdgeInsets.only(bottom: 10),
                      padding: const EdgeInsets.all(10),
                      decoration: BoxDecoration(
                        color: selected
                            ? AppColors.brandTint
                            : AppColors.surfaceSubtle,
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(
                          color: selected ? AppColors.brand : AppColors.line,
                          width: selected ? 1.5 : 1.0,
                        ),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Container(
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 7,
                                  vertical: 2,
                                ),
                                decoration: BoxDecoration(
                                  color: selected
                                      ? AppColors.brand
                                      : AppColors.ink,
                                  borderRadius: BorderRadius.circular(999),
                                ),
                                child: Text(
                                  '#${index + 1}',
                                  style: const TextStyle(
                                    color: Colors.white,
                                    fontSize: 11,
                                    fontWeight: FontWeight.w800,
                                  ),
                                ),
                              ),
                              const SizedBox(width: 8),
                              Expanded(
                                child: SelectableText(
                                  region.sourceText,
                                  style: const TextStyle(
                                    fontSize: 12.5,
                                    color: AppColors.muted,
                                  ),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 6),
                          TextFormField(
                            key: ValueKey('region_edit_${region.id}'),
                            initialValue: region.translation ?? '',
                            minLines: 1,
                            maxLines: 3,
                            onTap: () {
                              if (_selectedRegionId != region.id) {
                                setState(() => _selectedRegionId = region.id);
                              }
                            },
                            onChanged: (value) =>
                                _updateRegionTranslation(region.id, value),
                            style: const TextStyle(
                              fontSize: 13.5,
                              fontWeight: FontWeight.w600,
                              color: AppColors.ink,
                            ),
                            decoration: const InputDecoration(
                              isDense: true,
                              contentPadding: EdgeInsets.symmetric(
                                horizontal: 10,
                                vertical: 8,
                              ),
                              hintText: 'Bản dịch trên ảnh…',
                            ),
                          ),
                        ],
                      ),
                    ),
                  );
                },
              ),
          ],
        ),
      ),
    );
  }
}
