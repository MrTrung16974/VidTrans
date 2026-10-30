import 'package:flutter/material.dart';

/// Publish modes accepted by `POST /api/v1/jobs/{job_id}/social-publishes`.
class PublishMode {
  const PublishMode._();

  static const directNow = 'DIRECT_NOW';
  static const scheduleLocal = 'SCHEDULE_LOCAL';
  static const draft = 'DRAFT';

  static const labels = {
    directNow: 'Đăng ngay',
    scheduleLocal: 'Hẹn giờ đăng',
    draft: 'Gửi bản nháp',
  };
}

/// A platform the web app can publish to (web/js/tiktok.js, instagram.js,
/// social-extra.js), with the same modes and options as the web forms.
class SocialPlatform {
  const SocialPlatform({
    required this.slug,
    required this.code,
    required this.name,
    required this.icon,
    required this.color,
    required this.modes,
    this.requiresConnection = true,
  });

  /// Path segment of `/api/v1/{slug}-auth`.
  final String slug;

  /// `platform` field of the publish request.
  final String code;
  final String name;
  final IconData icon;
  final Color color;
  final List<String> modes;

  /// Instagram, Facebook and YouTube must name a connection (backend 422 otherwise).
  final bool requiresConnection;

  static const tiktok = SocialPlatform(
    slug: 'tiktok',
    code: 'TIKTOK',
    name: 'TikTok',
    icon: Icons.music_note_rounded,
    color: Color(0xFF111827),
    modes: [PublishMode.directNow, PublishMode.scheduleLocal, PublishMode.draft],
    requiresConnection: false,
  );
  static const instagram = SocialPlatform(
    slug: 'instagram',
    code: 'INSTAGRAM',
    name: 'Instagram',
    icon: Icons.camera_alt_rounded,
    color: Color(0xFFC13584),
    modes: [PublishMode.directNow, PublishMode.scheduleLocal],
  );
  static const facebook = SocialPlatform(
    slug: 'facebook',
    code: 'FACEBOOK',
    name: 'Facebook',
    icon: Icons.facebook_rounded,
    color: Color(0xFF1877F2),
    modes: [PublishMode.directNow, PublishMode.scheduleLocal],
  );
  static const youtube = SocialPlatform(
    slug: 'youtube',
    code: 'YOUTUBE',
    name: 'YouTube',
    icon: Icons.smart_display_rounded,
    color: Color(0xFFFF0000),
    modes: [PublishMode.directNow, PublishMode.scheduleLocal],
  );

  static const all = [tiktok, instagram, facebook, youtube];

  static SocialPlatform? byCode(String? code) {
    for (final platform in all) {
      if (platform.code == code?.toUpperCase()) return platform;
    }
    return null;
  }
}

/// TikTok privacy levels offered by the web form (`#tiktokPrivacy`).
const tiktokPrivacyLabels = {
  'SELF_ONLY': 'Chỉ mình tôi',
  'MUTUAL_FOLLOW_FRIENDS': 'Bạn bè',
  'FOLLOWER_OF_CREATOR': 'Người theo dõi',
  'PUBLIC_TO_EVERYONE': 'Mọi người',
};

/// YouTube privacy options offered by the web form (`#youtubePrivacy`).
const youtubePrivacyLabels = {
  'private': 'Riêng tư',
  'unlisted': 'Không công khai',
  'public': 'Công khai',
};

class SocialAccount {
  const SocialAccount({required this.accountId, this.connectionId, required this.name});

  final String accountId;
  final String? connectionId;
  final String name;

  /// Value identifying the account in a picker.
  String get key => (connectionId?.isNotEmpty ?? false) ? connectionId! : accountId;

  factory SocialAccount.fromJson(Map<String, dynamic> json) {
    final accountId = (json['account_id'] ?? json['open_id'] ?? '').toString();
    final username = json['username'] as String?;
    final connectionId = json['connection_id']?.toString().trim();
    return SocialAccount(
      accountId: accountId,
      connectionId: (connectionId == null || connectionId.isEmpty) ? null : connectionId,
      name: (json['name'] as String?) ??
          (json['display_name'] as String?) ??
          (username != null ? '@$username' : null) ??
          (json['open_id'] as String?) ??
          accountId,
    );
  }
}

/// `GET /api/v1/{slug}-auth`.
class SocialAuthStatus {
  const SocialAuthStatus({
    required this.configured,
    required this.connected,
    required this.accounts,
    this.canDirectPublish,
    this.connectionId,
    this.accountId,
  });

  final bool configured;
  final bool connected;
  final List<SocialAccount> accounts;

  /// TikTok only: false when the connection lacks the publish scope.
  final bool? canDirectPublish;
  final String? connectionId;
  final String? accountId;

  /// Account picked by default, like the web select.
  SocialAccount? get defaultAccount {
    for (final account in accounts) {
      if (account.connectionId != null && account.connectionId == connectionId) return account;
      if (account.accountId == accountId) return account;
    }
    return accounts.isEmpty ? null : accounts.first;
  }

  factory SocialAuthStatus.fromJson(Map<String, dynamic> json) => SocialAuthStatus(
        configured: json['configured'] == true,
        connected: json['connected'] == true,
        accounts: [
          for (final item in (json['accounts'] as List? ?? const []))
            if (item is Map) SocialAccount.fromJson(Map<String, dynamic>.from(item)),
        ],
        canDirectPublish: json['can_direct_publish'] as bool?,
        connectionId: json['connection_id'] as String?,
        accountId: json['account_id']?.toString(),
      );
}

/// `GET /api/v1/jobs/{job_id}/tiktok-draft`: caption prepared for a finished video.
class SocialDraft {
  const SocialDraft({
    required this.jobId,
    required this.filename,
    required this.caption,
    required this.youtubeTitle,
    this.videoUrl,
  });

  final String jobId;
  final String filename;
  final String caption;
  final String youtubeTitle;
  final String? videoUrl;

  factory SocialDraft.fromJson(Map<String, dynamic> json) => SocialDraft(
        jobId: json['job_id'].toString(),
        filename: json['filename'] as String? ?? '',
        caption: json['caption'] as String? ?? '',
        youtubeTitle: json['youtube_title'] as String? ?? '',
        videoUrl: json['video_url'] as String?,
      );
}

/// One publish request and its progress on the platform.
class PublishAttempt {
  const PublishAttempt(this.raw);

  final Map<String, dynamic> raw;

  String get id => raw['attempt_id'].toString();
  String get platformCode => raw['platform'] as String? ?? '';
  SocialPlatform? get platform => SocialPlatform.byCode(platformCode);
  String get status => raw['status'] as String? ?? 'READY';
  String? get mode => raw['mode'] as String?;
  String? get errorMessage => raw['error_message'] as String?;
  bool get hasRemote => raw['remote_media_id'] != null || raw['remote_post_id'] != null;
  DateTime? get scheduledFor => DateTime.tryParse(raw['scheduled_for']?.toString() ?? '')?.toLocal();
  DateTime? get createdAt => DateTime.tryParse(raw['created_at']?.toString() ?? '')?.toLocal();

  int? get percent {
    final detail = raw['status_detail'];
    return detail is Map ? (detail['percent'] as num?)?.toInt() : null;
  }

  bool get isActive => activeStatuses.contains(status);

  /// Still moving on the server, so the screen polls it (a schedule just waits).
  bool get isInFlight => isActive && status != 'SCHEDULED_LOCAL';

  bool get canCancel => status == 'SCHEDULED_LOCAL';

  String get label => statusLabels[status] ?? status;

  static const activeStatuses = {
    'READY', 'SCHEDULED_LOCAL', 'CLAIMED', 'UPLOADING', 'PROCESSING_REMOTE', 'SUBMITTING', 'NEEDS_RECONCILIATION',
  };

  static const statusLabels = {
    'READY': 'Đang chuẩn bị',
    'CLAIMED': 'Đang chuẩn bị',
    'SCHEDULED_LOCAL': 'Đã đặt lịch',
    'UPLOADING': 'Đang tải video',
    'PROCESSING_REMOTE': 'Nền tảng đang xử lý',
    'SUBMITTING': 'Đang gửi',
    'PUBLISHED': 'Đã đăng thành công',
    'DRAFT_DELIVERED': 'Đã gửi bản nháp',
    'FAILED': 'Đăng thất bại',
    'CANCELLED': 'Đã hủy',
    'NEEDS_RECONCILIATION': 'Cần đối soát',
  };

  factory PublishAttempt.fromJson(Map<String, dynamic> json) => PublishAttempt(json);
}

/// Body of `POST /api/v1/jobs/{job_id}/social-publishes`, built the way the
/// web forms build it so both clients queue identical publishes.
Map<String, dynamic> buildPublishPayload({
  required SocialPlatform platform,
  required SocialAccount? account,
  required String mode,
  required String caption,
  required String idempotencyKey,
  required String timezone,
  DateTime? scheduledFor,
  String tiktokPrivacy = 'SELF_ONLY',
  String youtubeTitle = '',
  String youtubePrivacy = 'private',
}) {
  final options = switch (platform.code) {
    'TIKTOK' => {'privacy_level': tiktokPrivacy},
    'YOUTUBE' => {'title': youtubeTitle.trim(), 'privacy': youtubePrivacy, 'category_id': '22'},
    _ => <String, dynamic>{},
  };
  return {
    'platform': platform.code,
    'mode': mode,
    'caption': caption,
    'timezone': timezone,
    'options': options,
    'reviewed': true,
    'idempotency_key': idempotencyKey,
    'scheduled_for': mode == PublishMode.scheduleLocal ? scheduledFor?.toUtc().toIso8601String() : null,
    if (account?.connectionId != null)
      'connection_id': account!.connectionId
    else if (account != null)
      'account_id': account.accountId,
  };
}

/// Reason the publish button is disabled, or null when the form is complete.
String? publishBlocker({
  required SocialPlatform platform,
  required SocialAuthStatus? auth,
  required SocialAccount? account,
  required String mode,
  required String caption,
  required bool reviewed,
  DateTime? scheduledFor,
  String youtubeTitle = '',
  DateTime? now,
}) {
  if (auth == null) return 'Đang tải trạng thái kết nối';
  if (!auth.configured) return '${platform.name} chưa được cấu hình trên máy chủ';
  if (!auth.connected || account == null) return 'Hãy kết nối tài khoản ${platform.name}';
  if (platform.code == 'TIKTOK' && mode != PublishMode.draft && auth.canDirectPublish == false) {
    return 'Kết nối TikTok chưa có quyền đăng video, hãy kết nối lại';
  }
  if (caption.length > 2200) return 'Nội dung dài quá 2.200 ký tự';
  if (platform.code == 'INSTAGRAM' && caption.trim().isEmpty) return 'Instagram cần có nội dung bài đăng';
  if (platform.code == 'YOUTUBE' && youtubeTitle.trim().isEmpty) return 'Nhập tiêu đề YouTube';
  if (mode == PublishMode.scheduleLocal) {
    if (scheduledFor == null) return 'Chọn thời gian đăng';
    if (!scheduledFor.isAfter(now ?? DateTime.now())) return 'Thời gian hẹn phải ở tương lai';
  }
  if (!reviewed) return 'Hãy xác nhận đã xem lại video và nội dung';
  return null;
}
