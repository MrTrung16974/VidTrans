import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:uuid/uuid.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/error_view.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../data/social_repository.dart';
import '../../domain/social_models.dart';
import '../providers/social_providers.dart';
import '../widgets/social_widgets.dart';

/// Publish one finished video: caption, platform, account, mode, then follow
/// every publish attempt of the video.
class SocialPublishScreen extends ConsumerWidget {
  const SocialPublishScreen({super.key, required this.jobId});

  final String jobId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final draft = ref.watch(socialDraftProvider(jobId));
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('Đăng video')),
      body: draft.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => ErrorView(
          message: errorMessage(error),
          onRetry: () => ref.invalidate(socialDraftProvider(jobId)),
        ),
        data: (draft) => _PublishForm(draft: draft),
      ),
    );
  }
}

class _PublishForm extends ConsumerStatefulWidget {
  const _PublishForm({required this.draft});

  final SocialDraft draft;

  @override
  ConsumerState<_PublishForm> createState() => _PublishFormState();
}

class _PublishFormState extends ConsumerState<_PublishForm> {
  late final _caption = TextEditingController(text: widget.draft.caption);
  late final _youtubeTitle = TextEditingController(text: widget.draft.youtubeTitle);
  SocialPlatform _platform = SocialPlatform.tiktok;
  String? _accountKey;
  String _mode = PublishMode.directNow;
  DateTime? _scheduledFor;
  String _tiktokPrivacy = 'SELF_ONLY';
  String _youtubePrivacy = 'private';
  bool _reviewed = false;
  bool _submitting = false;
  bool _suggesting = false;

  /// One key per publish intent: kept when the user retries after a network
  /// error, replaced as soon as anything in the form changes (like the web).
  String? _idempotencyKey;
  Timer? _poll;

  String get _jobId => widget.draft.jobId;

  @override
  void dispose() {
    _poll?.cancel();
    _caption.dispose();
    _youtubeTitle.dispose();
    super.dispose();
  }

  void _changed(VoidCallback update) {
    setState(() {
      update();
      _reviewed = false;
      _idempotencyKey = null;
    });
  }

  SocialAccount? _account(SocialAuthStatus? auth) {
    if (auth == null) return null;
    for (final account in auth.accounts) {
      if (account.key == _accountKey) return account;
    }
    return auth.defaultAccount;
  }

  Future<void> _suggestCaption() async {
    setState(() => _suggesting = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final caption = await ref.read(socialRepositoryProvider).suggestCaption(_jobId);
      if (caption.isNotEmpty) _changed(() => _caption.text = caption);
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    } finally {
      if (mounted) setState(() => _suggesting = false);
    }
  }

  Future<void> _pickSchedule() async {
    final now = DateTime.now();
    final initial = _scheduledFor ?? now.add(const Duration(hours: 1));
    final date = await showDatePicker(
      context: context,
      firstDate: now,
      lastDate: now.add(const Duration(days: 365)),
      initialDate: initial,
    );
    if (date == null || !mounted) return;
    final time = await showTimePicker(context: context, initialTime: TimeOfDay.fromDateTime(initial));
    if (time == null) return;
    _changed(() => _scheduledFor = DateTime(date.year, date.month, date.day, time.hour, time.minute));
  }

  Future<void> _publish(SocialAuthStatus auth) async {
    final account = _account(auth);
    _idempotencyKey ??= const Uuid().v4();
    setState(() => _submitting = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final payload = buildPublishPayload(
        platform: _platform,
        account: account,
        mode: _mode,
        caption: _caption.text,
        idempotencyKey: _idempotencyKey!,
        timezone: _timezone(),
        scheduledFor: _scheduledFor,
        tiktokPrivacy: _tiktokPrivacy,
        youtubeTitle: _youtubeTitle.text,
        youtubePrivacy: _youtubePrivacy,
      );
      await ref.read(socialRepositoryProvider).publish(_jobId, payload);
      ref.invalidate(publishAttemptsProvider(_jobId));
      setState(() {
        _reviewed = false;
        _idempotencyKey = null;
      });
      messenger.showSnackBar(SnackBar(
        content: Text(_mode == PublishMode.scheduleLocal
            ? 'Đã lưu lịch đăng ${_platform.name}'
            : 'Đang gửi video lên ${_platform.name}'),
      ));
    } catch (error) {
      // A rejected request (4xx except 409) will not succeed with the same key.
      final status = error is ApiException ? error.statusCode : null;
      if (status != null && status < 500 && status != 409) _idempotencyKey = null;
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  /// Stored with the attempt for display only; the schedule itself is sent as
  /// an absolute UTC time in `scheduled_for`.
  String _timezone() {
    final offset = DateTime.now().timeZoneOffset;
    if (offset == const Duration(hours: 7)) return 'Asia/Ho_Chi_Minh';
    if (offset == Duration.zero) return 'UTC';
    return DateTime.now().timeZoneName;
  }

  void _schedulePolling(List<PublishAttempt> attempts) {
    final inFlight = attempts.any((attempt) => attempt.isInFlight);
    if (inFlight && _poll == null) {
      _poll = Timer.periodic(const Duration(seconds: 5), (_) => ref.invalidate(publishAttemptsProvider(_jobId)));
    } else if (!inFlight && _poll != null) {
      _poll!.cancel();
      _poll = null;
    }
  }

  @override
  Widget build(BuildContext context) {
    final auth = ref.watch(socialAuthProvider(_platform));
    final attempts = ref.watch(publishAttemptsProvider(_jobId));
    final readOnly = ref.watch(sessionProvider).valueOrNull?.isReadOnly ?? true;
    ref.listen(publishAttemptsProvider(_jobId), (_, next) => _schedulePolling(next.valueOrNull ?? const []));

    final status = auth.valueOrNull;
    final account = _account(status);
    final blocker = publishBlocker(
      platform: _platform,
      auth: status,
      account: account,
      mode: _mode,
      caption: _caption.text,
      reviewed: _reviewed,
      scheduledFor: _scheduledFor,
      youtubeTitle: _youtubeTitle.text,
    );
    final text = Theme.of(context).textTheme;

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 40),
      children: [
        StudioCard(
          child: Row(
            children: [
              const Icon(Icons.movie_rounded, color: AppColors.brand),
              const SizedBox(width: 12),
              Expanded(child: Text(widget.draft.filename, style: text.titleSmall, maxLines: 2, overflow: TextOverflow.ellipsis)),
              TextButton(onPressed: () => context.push('/jobs/$_jobId/player'), child: const Text('Xem')),
            ],
          ),
        ),
        const SizedBox(height: 16),
        const StudioStepHeader(stepNumber: 1, icon: Icons.edit_note_rounded, title: 'Nội dung bài đăng'),
        const SizedBox(height: 8),
        TextField(
          controller: _caption,
          enabled: !readOnly,
          minLines: 4,
          maxLines: 10,
          maxLength: 2200,
          onChanged: (_) => _changed(() {}),
          decoration: const InputDecoration(
            hintText: 'Caption, hashtag…',
            border: OutlineInputBorder(),
          ),
        ),
        if (!readOnly)
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: _suggesting ? null : _suggestCaption,
              icon: _suggesting
                  ? const SizedBox.square(dimension: 16, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.auto_awesome_rounded, size: 18),
              label: const Text('Gợi ý caption từ bản dịch'),
            ),
          ),
        const SizedBox(height: 12),
        const StudioStepHeader(stepNumber: 2, icon: Icons.share_rounded, title: 'Nền tảng & tài khoản'),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final platform in SocialPlatform.all)
              ChoiceChip(
                avatar: Icon(platform.icon, size: 18, color: platform == _platform ? Colors.white : platform.color),
                label: Text(platform.name),
                selected: platform == _platform,
                selectedColor: platform.color,
                labelStyle: TextStyle(color: platform == _platform ? Colors.white : null),
                onSelected: (_) => _changed(() {
                  _platform = platform;
                  _accountKey = null;
                  if (!platform.modes.contains(_mode)) _mode = PublishMode.directNow;
                }),
              ),
          ],
        ),
        const SizedBox(height: 12),
        auth.when(
          loading: () => const LinearProgressIndicator(),
          error: (error, _) => Text(errorMessage(error), style: const TextStyle(color: AppColors.redDark)),
          data: (status) => status.accounts.isEmpty
              ? OutlinedButton.icon(
                  onPressed: () => context.go('/social'),
                  icon: const Icon(Icons.add_link_rounded),
                  label: Text('Kết nối tài khoản ${_platform.name}'),
                )
              : DropdownButtonFormField<String>(
                  initialValue: account?.key,
                  decoration: const InputDecoration(labelText: 'Tài khoản', border: OutlineInputBorder()),
                  items: [
                    for (final item in status.accounts) DropdownMenuItem(value: item.key, child: Text(item.name)),
                  ],
                  onChanged: readOnly ? null : (value) => _changed(() => _accountKey = value),
                ),
        ),
        const SizedBox(height: 12),
        SegmentedButton<String>(
          segments: [
            for (final mode in _platform.modes)
              ButtonSegment(value: mode, label: Text(PublishMode.labels[mode]!, textAlign: TextAlign.center)),
          ],
          selected: {_mode},
          onSelectionChanged: readOnly ? null : (value) => _changed(() => _mode = value.first),
        ),
        if (_mode == PublishMode.scheduleLocal) ...[
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: readOnly ? null : _pickSchedule,
            icon: const Icon(Icons.schedule_rounded),
            label: Text(_scheduledFor == null
                ? 'Chọn thời gian đăng'
                : 'Đăng lúc ${DateFormat('HH:mm dd/MM/yyyy').format(_scheduledFor!)}'),
          ),
        ],
        if (_platform.code == 'TIKTOK') ...[
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            initialValue: _tiktokPrivacy,
            decoration: const InputDecoration(labelText: 'Ai được xem', border: OutlineInputBorder()),
            items: [
              for (final entry in tiktokPrivacyLabels.entries)
                DropdownMenuItem(value: entry.key, child: Text(entry.value)),
            ],
            onChanged: readOnly ? null : (value) => _changed(() => _tiktokPrivacy = value!),
          ),
        ],
        if (_platform.code == 'YOUTUBE') ...[
          const SizedBox(height: 12),
          TextField(
            controller: _youtubeTitle,
            enabled: !readOnly,
            maxLength: 100,
            onChanged: (_) => _changed(() {}),
            decoration: const InputDecoration(labelText: 'Tiêu đề YouTube', border: OutlineInputBorder()),
          ),
          DropdownButtonFormField<String>(
            initialValue: _youtubePrivacy,
            decoration: const InputDecoration(labelText: 'Chế độ hiển thị', border: OutlineInputBorder()),
            items: [
              for (final entry in youtubePrivacyLabels.entries)
                DropdownMenuItem(value: entry.key, child: Text(entry.value)),
            ],
            onChanged: readOnly ? null : (value) => _changed(() => _youtubePrivacy = value!),
          ),
        ],
        if (!readOnly) ...[
          const SizedBox(height: 8),
          CheckboxListTile(
            contentPadding: EdgeInsets.zero,
            controlAffinity: ListTileControlAffinity.leading,
            value: _reviewed,
            onChanged: (value) => setState(() => _reviewed = value ?? false),
            title: const Text('Tôi đã xem lại video, nội dung và tài khoản đăng'),
          ),
          if (blocker != null && blocker != 'Hãy xác nhận đã xem lại video và nội dung')
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(blocker, style: const TextStyle(color: AppColors.muted)),
            ),
          StudioGradientButton(
            onPressed: blocker != null || _submitting || status == null ? null : () => _publish(status),
            isLoading: _submitting,
            icon: Icons.send_rounded,
            label: _mode == PublishMode.scheduleLocal
                ? 'Đặt lịch ${_platform.name}'
                : _mode == PublishMode.draft
                    ? 'Gửi bản nháp ${_platform.name}'
                    : 'Đăng lên ${_platform.name}',
          ),
        ],
        const SizedBox(height: 24),
        const StudioStepHeader(stepNumber: 3, icon: Icons.history_rounded, title: 'Các lần đăng của video này'),
        const SizedBox(height: 8),
        attempts.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (error, _) => Text(errorMessage(error)),
          data: (items) => items.isEmpty
              ? const Text('Chưa đăng lần nào.', style: TextStyle(color: AppColors.muted))
              : Column(children: [for (final attempt in items) _AttemptTile(attempt: attempt, jobId: _jobId)]),
        ),
      ],
    );
  }
}

class _AttemptTile extends ConsumerWidget {
  const _AttemptTile({required this.attempt, required this.jobId});

  final PublishAttempt attempt;
  final String jobId;

  Future<void> _run(BuildContext context, WidgetRef ref, Future<void> Function() action) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await action();
      ref.invalidate(publishAttemptsProvider(jobId));
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(errorMessage(error))));
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final platform = attempt.platform;
    final repo = ref.read(socialRepositoryProvider);
    final detail = attempt.errorMessage ??
        (attempt.status == 'SCHEDULED_LOCAL' && attempt.scheduledFor != null
            ? 'VidTrans sẽ đăng lúc ${DateFormat('HH:mm dd/MM/yyyy').format(attempt.scheduledFor!)}'
            : attempt.status == 'UPLOADING' && attempt.percent != null
                ? 'Đang gửi · ${attempt.percent}%'
                : null);

    return StudioCard(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              if (platform != null) PlatformAvatar(platform, size: 28),
              const SizedBox(width: 10),
              Expanded(child: Text(platform?.name ?? attempt.platformCode, style: Theme.of(context).textTheme.titleSmall)),
              StudioStatusPill(label: attempt.label, tone: attemptTone(attempt), compact: true),
            ],
          ),
          if (detail != null) ...[
            const SizedBox(height: 6),
            Text(detail, style: TextStyle(color: attempt.status == 'FAILED' ? AppColors.redDark : AppColors.muted)),
          ],
          if (attempt.isInFlight) ...[
            const SizedBox(height: 8),
            LinearProgressIndicator(value: attempt.percent == null ? null : attempt.percent! / 100),
          ],
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              if (attempt.createdAt != null)
                Expanded(
                  child: Text(DateFormat('dd/MM HH:mm').format(attempt.createdAt!),
                      style: Theme.of(context).textTheme.bodySmall),
                ),
              if (attempt.isActive)
                TextButton(
                  onPressed: () => _run(context, ref, () => repo.refresh(attempt)),
                  child: const Text('Làm mới'),
                ),
              if (attempt.canCancel)
                TextButton(
                  onPressed: () => _run(context, ref, () => repo.cancel(attempt.id)),
                  child: const Text('Hủy lịch'),
                ),
            ],
          ),
        ],
      ),
    );
  }
}
