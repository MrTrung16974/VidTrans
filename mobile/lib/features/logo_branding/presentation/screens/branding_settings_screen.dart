import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../domain/branding_models.dart';
import '../providers/branding_providers.dart';
import '../widgets/auth_image.dart';
import '../widgets/branding_widgets.dart';

/// Account logo, the sample of the old logo to replace, and how the logo is stamped.
class BrandingSettingsScreen extends ConsumerWidget {
  const BrandingSettingsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final settings = ref.watch(brandingSettingsProvider);
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('Cấu hình logo')),
      body: settings.when(
        skipLoadingOnRefresh: true,
        skipLoadingOnReload: true,
        loading: () => const Center(
          child: CircularProgressIndicator(color: AppColors.brand),
        ),
        error: (error, _) => ErrorView(
          message: errorMessage(error),
          onRetry: () => ref.invalidate(brandingSettingsProvider),
        ),
        data: (settings) => _SettingsForm(settings: settings),
      ),
    );
  }
}

class _SettingsForm extends ConsumerStatefulWidget {
  const _SettingsForm({required this.settings});

  final BrandingSettings settings;

  @override
  ConsumerState<_SettingsForm> createState() => _SettingsFormState();
}

class _SettingsFormState extends ConsumerState<_SettingsForm> {
  late BrandingConfig _config = widget.settings.config;
  String? _busy;

  BrandingSettingsController get _controller =>
      ref.read(brandingSettingsProvider.notifier);

  Future<void> _run(
    String busy,
    Future<void> Function() action,
    String done,
  ) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _busy = busy);
    try {
      await action();
      messenger.showSnackBar(SnackBar(content: Text(done)));
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  Future<void> _upload(String kind, String done) async {
    final files = await FilePicker.pickFiles(type: FileType.image);
    if (files.isEmpty) return;
    final path = files.first.path ?? files.first.xFile.path;
    await _run(kind, () => _controller.uploadImage(kind, path), done);
  }

  Future<void> _deleteLogo() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        content: const Text('Xóa logo của tài khoản?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Không'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Xóa'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await _run('logo', () => _controller.deleteImage('logo'), 'Đã xóa logo');
    }
  }

  void _set(BrandingConfig config) => setState(() => _config = config);

  @override
  Widget build(BuildContext context) {
    final settings =
        ref.watch(brandingSettingsProvider).valueOrNull ?? widget.settings;
    final readOnly = ref.watch(sessionProvider).valueOrNull?.isReadOnly ?? true;
    final enabled = !readOnly && _busy == null;

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 32),
      children: [
        _ImageCard(
          title: 'Logo thương hiệu',
          image: settings.logo,
          emptyText: 'Chưa có logo',
          busy: _busy == 'logo',
          onUpload: enabled ? () => _upload('logo', 'Đã cập nhật logo') : null,
          onDelete: enabled && settings.logo != null ? _deleteLogo : null,
        ),
        const SizedBox(height: 12),
        _ImageCard(
          title: 'Logo mẫu cần thay',
          subtitle: 'Ảnh cắt từ logo cũ trong video, giúp dò đúng vị trí.',
          image: settings.template,
          emptyText: 'Chưa có mẫu',
          busy: _busy == 'template',
          onUpload:
              enabled ? () => _upload('template', 'Đã lưu logo mẫu') : null,
          onDelete: enabled && settings.template != null
              ? () => _run(
                    'template',
                    () => _controller.deleteImage('template'),
                    'Đã xóa logo mẫu',
                  )
              : null,
        ),
        const SizedBox(height: 14),
        StudioCard(
          topAccentColor: AppColors.teal,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const StudioStepHeader(
                icon: Icons.preview_outlined,
                accentColor: AppColors.teal,
                title: 'Xem trước vị trí đóng dấu',
                subtitle: 'Mô phỏng tỷ lệ thật trên khung 16:9 ngang & 9:16 dọc',
              ),
              const SizedBox(height: 14),
              LogoPlacementPreview(config: _config, logo: settings.logo),
            ],
          ),
        ),
        const SizedBox(height: 14),
        StudioCard(
          topAccentColor: AppColors.brand,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const StudioStepHeader(
                icon: Icons.tune_rounded,
                title: 'Cách chèn logo',
                subtitle: 'Tuỳ chỉnh tự động nhận diện, kích thước và xoá logo cũ',
              ),
              const SizedBox(height: 12),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Tự động nhận diện vị trí logo có sẵn'),
                value: _config.autoDetect,
                onChanged: enabled
                    ? (value) => _set(_config.copyWith(autoDetect: value))
                    : null,
              ),
              _Dropdown(
                label: 'Vị trí mặc định khi không tìm thấy logo',
                value: _config.defaultPosition,
                options: positionLabels,
                onChanged: enabled
                    ? (value) => _set(_config.copyWith(defaultPosition: value))
                    : null,
              ),
              _SliderRow(
                label: 'Kích thước logo (% chiều rộng video)',
                display: '${_config.logoSize.round()}%',
                value: _config.logoSize,
                min: 3,
                max: 40,
                divisions: 37,
                onChanged: enabled
                    ? (value) =>
                        _set(_config.copyWith(logoSize: value.roundToDouble()))
                    : null,
              ),
              _SliderRow(
                label: 'Độ hiển thị',
                display: '${(_config.opacity * 100).round()}%',
                value: _config.opacity * 100,
                min: 10,
                max: 100,
                divisions: 18,
                onChanged: enabled
                    ? (value) => _set(
                          _config.copyWith(
                            opacity: (value / 5).round() * 5 / 100,
                          ),
                        )
                    : null,
              ),
              _SliderRow(
                label: 'Khoảng cách tới mép (% cạnh ngắn)',
                display: '${_config.margin}%',
                value: _config.margin,
                min: 0,
                max: 15,
                divisions: 30,
                onChanged: enabled
                    ? (value) =>
                        _set(_config.copyWith(margin: (value * 2).round() / 2))
                    : null,
              ),
              _Dropdown(
                label: 'Khi video có nhiều logo, thay logo',
                value: _config.multiLogoStrategy,
                options: multiLogoStrategyLabels,
                onChanged: enabled
                    ? (value) =>
                        _set(_config.copyWith(multiLogoStrategy: value))
                    : null,
              ),
              _Dropdown(
                label: 'Khi không chắc chắn vị trí',
                value: _config.lowConfidenceAction,
                options: lowConfidenceActionLabels,
                onChanged: enabled
                    ? (value) =>
                        _set(_config.copyWith(lowConfidenceAction: value))
                    : null,
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Xóa logo cũ trước khi chèn logo mới'),
                value: _config.coverOriginal,
                onChanged: enabled
                    ? (value) => _set(_config.copyWith(coverOriginal: value))
                    : null,
              ),
              _Dropdown(
                label: 'Cách xóa logo cũ',
                value: _config.coverMethod,
                options: coverMethodLabels,
                onChanged: enabled && _config.coverOriginal
                    ? (value) => _set(_config.copyWith(coverMethod: value))
                    : null,
              ),
              if (!readOnly) ...[
                const SizedBox(height: 16),
                StudioGradientButton(
                  onPressed: _busy == null
                      ? () => _run(
                            'config',
                            () => _controller.saveConfig(_config),
                            'Đã lưu cấu hình logo',
                          )
                      : null,
                  isLoading: _busy == 'config',
                  icon: Icons.save_outlined,
                  label: 'Lưu cấu hình',
                ),
              ],
            ],
          ),
        ),
      ],
    );
  }
}

class _ImageCard extends StatelessWidget {
  const _ImageCard({
    required this.title,
    required this.image,
    required this.emptyText,
    required this.busy,
    this.subtitle,
    this.onUpload,
    this.onDelete,
  });

  final String title;
  final String? subtitle;
  final BrandingImage? image;
  final String emptyText;
  final bool busy;
  final VoidCallback? onUpload;
  final VoidCallback? onDelete;

  @override
  Widget build(BuildContext context) {
    final image = this.image;
    return StudioCard(
      padding: const EdgeInsets.all(14),
      child: Row(
        children: [
          Container(
            width: 84,
            height: 84,
            decoration: BoxDecoration(
              color: AppColors.surfaceSubtle,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: AppColors.lineStrong),
            ),
            alignment: Alignment.center,
            child: busy
                ? const CircularProgressIndicator(color: AppColors.brand)
                : image == null
                    ? Text(
                        emptyText,
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                          fontSize: 11.5,
                          color: AppColors.muted,
                        ),
                      )
                    : Padding(
                        padding: const EdgeInsets.all(6),
                        child: AuthImage(image.url),
                      ),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    fontSize: 14.5,
                    fontWeight: FontWeight.w800,
                    color: AppColors.ink,
                  ),
                ),
                if (subtitle != null) ...[
                  const SizedBox(height: 2),
                  Text(
                    subtitle!,
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.muted,
                    ),
                  ),
                ],
                if (image != null) ...[
                  const SizedBox(height: 4),
                  Text(
                    '${image.width}×${image.height}px${image.transparent ? ' · nền trong suốt' : ''}',
                    style: const TextStyle(
                      fontSize: 11.5,
                      fontWeight: FontWeight.w600,
                      color: AppColors.tealText,
                    ),
                  ),
                ],
                Row(
                  children: [
                    TextButton.icon(
                      onPressed: onUpload,
                      icon: const Icon(Icons.upload_outlined, size: 16),
                      label: Text(image == null ? 'Tải lên' : 'Đổi ảnh'),
                    ),
                    if (image != null)
                      TextButton(
                        onPressed: onDelete,
                        child: const Text(
                          'Xóa',
                          style: TextStyle(color: AppColors.red),
                        ),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _Dropdown extends StatelessWidget {
  const _Dropdown({
    required this.label,
    required this.value,
    required this.options,
    this.onChanged,
  });

  final String label;
  final String value;
  final Map<String, String> options;
  final ValueChanged<String>? onChanged;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: DropdownButtonFormField<String>(
        key: ValueKey('$label$value'),
        initialValue: options.containsKey(value) ? value : null,
        isExpanded: true,
        decoration: InputDecoration(labelText: label),
        items: [
          for (final entry in options.entries)
            DropdownMenuItem(
              value: entry.key,
              child: Text(entry.value, overflow: TextOverflow.ellipsis),
            ),
        ],
        onChanged: onChanged == null
            ? null
            : (value) => value == null ? null : onChanged!(value),
      ),
    );
  }
}

class _SliderRow extends StatelessWidget {
  const _SliderRow({
    required this.label,
    required this.display,
    required this.value,
    required this.min,
    required this.max,
    required this.divisions,
    this.onChanged,
  });

  final String label;
  final String display;
  final double value;
  final double min;
  final double max;
  final int divisions;
  final ValueChanged<double>? onChanged;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Expanded(
                child: Text(
                  label,
                  style: const TextStyle(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w700,
                    color: AppColors.inkSoft,
                  ),
                ),
              ),
              StudioEyebrow(label: display),
            ],
          ),
          Slider(
            value: value.clamp(min, max),
            min: min,
            max: max,
            divisions: divisions,
            onChanged: onChanged,
          ),
        ],
      ),
    );
  }
}
