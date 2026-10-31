import 'package:dio/dio.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:uuid/uuid.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../data/branding_repository.dart';
import '../../domain/branding_models.dart';
import '../providers/branding_providers.dart';

/// Uploads one video to stamp the account logo on (same form as the web).
class BrandingUploadScreen extends ConsumerStatefulWidget {
  const BrandingUploadScreen({super.key});

  @override
  ConsumerState<BrandingUploadScreen> createState() =>
      _BrandingUploadScreenState();
}

class _BrandingUploadScreenState extends ConsumerState<BrandingUploadScreen> {
  PlatformFile? _file;
  String _override = 'auto';
  bool _rightsConfirmed = false;
  double? _progress;
  CancelToken? _cancelToken;
  String? _error;

  /// Kept across retries of the same submission so a request that reached the
  /// server before the network dropped is not queued twice.
  String? _idempotencyKey;

  bool get _uploading => _cancelToken != null;

  void _changed(VoidCallback update) => setState(() {
        update();
        _idempotencyKey = null;
        _error = null;
      });

  Future<void> _pick() async {
    final files = await FilePicker.pickFiles(type: FileType.video);
    if (files.isEmpty) return;
    _changed(() => _file = files.first);
  }

  Future<void> _submit() async {
    final file = _file;
    final path = file?.path ?? file?.xFile.path;
    if (file == null || path == null) return;
    final cancelToken = CancelToken();
    _idempotencyKey ??= const Uuid().v4();
    setState(() {
      _cancelToken = cancelToken;
      _progress = 0;
      _error = null;
    });
    try {
      final jobId = await ref.read(brandingRepositoryProvider).queueVideo(
            filePath: path,
            fileName: file.name,
            override: _override,
            rightsConfirmed: _rightsConfirmed,
            idempotencyKey: _idempotencyKey!,
            cancelToken: cancelToken,
            onProgress: (sent, total) {
              if (total > 0 && mounted) {
                setState(() => _progress = sent / total);
              }
            },
          );
      if (!mounted) return;
      ref.invalidate(brandingHistoryProvider);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Đã gửi video, đang chèn logo')),
      );
      context.pushReplacement('/branding/$jobId');
    } catch (error) {
      if (!mounted) return;
      setState(
        () => _error =
            cancelToken.isCancelled ? 'Đã hủy tải lên' : errorMessage(error),
      );
    } finally {
      if (mounted) {
        setState(() {
          _cancelToken = null;
          _progress = null;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final settings = ref.watch(brandingSettingsProvider);
    final hasLogo = settings.valueOrNull?.logo != null;
    final canSubmit =
        !_uploading && _file != null && _rightsConfirmed && hasLogo;

    return PopScope(
      canPop: !_uploading,
      child: Scaffold(
        backgroundColor: AppColors.bg,
        appBar: const StudioSubBar(
          title: 'Gửi video gắn logo',
          subtitle: 'Tự động nhận diện & đóng dấu thương hiệu',
        ),
        body: ListView(
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 32),
          children: [
            if (settings.hasValue && !hasLogo)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: StudioCard(
                  borderColor: AppColors.redBorder,
                  backgroundColor: AppColors.redSoft,
                  onTap: () => context.push('/branding/settings'),
                  child: const Row(
                    children: [
                      Icon(Icons.warning_amber_rounded, color: AppColors.red),
                      SizedBox(width: 10),
                      Expanded(
                        child: Text(
                          'Hãy tải logo thương hiệu lên trước khi gửi video',
                          style: TextStyle(
                            fontWeight: FontWeight.w700,
                            color: AppColors.redText,
                          ),
                        ),
                      ),
                      Icon(Icons.chevron_right_rounded, color: AppColors.red),
                    ],
                  ),
                ),
              ),
            StudioCard(
              topAccentColor: AppColors.brand,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const StudioStepHeader(
                    stepNumber: 1,
                    title: 'Chọn video & Vị trí gắn Logo',
                    subtitle: 'MP4, MOV, MKV, AVI, WEBM, M4V · tối đa 2048 MB',
                  ),
                  const SizedBox(height: 14),
                  Material(
                    color: AppColors.surfaceSubtle,
                    borderRadius: BorderRadius.circular(14),
                    child: InkWell(
                      onTap: _uploading ? null : _pick,
                      borderRadius: BorderRadius.circular(14),
                      child: Container(
                        padding: const EdgeInsets.all(16),
                        decoration: BoxDecoration(
                          borderRadius: BorderRadius.circular(14),
                          border: Border.all(
                            color: AppColors.brandBorder,
                            width: 1.3,
                          ),
                        ),
                        child: Row(
                          children: [
                            const Icon(
                              Icons.video_file_outlined,
                              color: AppColors.brand,
                              size: 26,
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Text(
                                _file?.name ?? 'Chạm để chọn video từ máy',
                                style: const TextStyle(
                                  fontWeight: FontWeight.w700,
                                  color: AppColors.ink,
                                ),
                              ),
                            ),
                            const Icon(
                              Icons.folder_open_rounded,
                              color: AppColors.muted,
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  DropdownButtonFormField<String>(
                    initialValue: _override,
                    decoration: const InputDecoration(labelText: 'Vị trí'),
                    items: [
                      for (final entry in overrideLabels.entries)
                        DropdownMenuItem(
                          value: entry.key,
                          child: Text(entry.value),
                        ),
                    ],
                    onChanged: _uploading
                        ? null
                        : (value) =>
                            _changed(() => _override = value ?? 'auto'),
                  ),
                  const SizedBox(height: 8),
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    value: _rightsConfirmed,
                    onChanged: _uploading
                        ? null
                        : (value) =>
                            _changed(() => _rightsConfirmed = value ?? false),
                    title: const Text(
                      'Tôi có quyền chỉnh sửa video này (kể cả thay/che logo có sẵn)',
                      style: TextStyle(fontSize: 13),
                    ),
                    controlAffinity: ListTileControlAffinity.leading,
                  ),
                ],
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(
                _error!,
                style: const TextStyle(
                  color: AppColors.red,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
            const SizedBox(height: 16),
            if (_uploading) ...[
              LinearProgressIndicator(value: _progress),
              const SizedBox(height: 8),
              Text('Đang tải lên ${((_progress ?? 0) * 100).round()}%'),
              const SizedBox(height: 8),
              OutlinedButton(
                onPressed: () => _cancelToken?.cancel(),
                child: const Text('Hủy tải lên'),
              ),
            ] else
              StudioGradientButton(
                onPressed: canSubmit ? _submit : null,
                icon: Icons.cloud_upload_outlined,
                label: _error == null ? 'Gửi video' : 'Thử lại',
              ),
          ],
        ),
      ),
    );
  }
}
