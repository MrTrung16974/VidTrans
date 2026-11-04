/// `GET /api/v1/me` (public_admin_account on the backend).
class Account {
  const Account({
    required this.userId,
    required this.email,
    required this.role,
    this.displayName,
    this.plan,
    this.approvalStatus,
    this.videoRetentionDays,
    this.features,
  });

  final String userId;
  final String email;
  final String role;
  final String? displayName;
  final String? plan;
  final String? approvalStatus;
  final int? videoRetentionDays;

  /// Plan features from `plan_entitlements` (backend), e.g. `comic_video`,
  /// `social_publishing`. Null when the server predates the field.
  final Set<String>? features;

  /// Viewers may only send read (GET) requests.
  bool get isReadOnly => role == 'viewer';

  /// Whether the plan includes [feature]. The server decides from the
  /// `plan_entitlements` table; trial and free have none of the paid features.
  bool hasFeature(String feature) {
    if (role == 'owner' || role == 'admin') return true;
    final granted = features;
    if (granted != null) return granted.contains(feature);
    // Older servers do not send `features`: only paid plans unlock anything.
    final current = (plan ?? 'free').trim().toLowerCase();
    return const {'creator', 'pro', 'enterprise'}.contains(current);
  }

  bool get canUseComics => hasFeature(PlanFeature.comicVideo);
  bool get canPublishSocial => hasFeature(PlanFeature.socialPublishing);

  String get name => (displayName?.isNotEmpty ?? false) ? displayName! : email;

  factory Account.fromJson(Map<String, dynamic> json) => Account(
    userId: json['user_id'].toString(),
    email: json['email'] as String? ?? '',
    role: json['role'] as String? ?? 'viewer',
    displayName: json['display_name'] as String?,
    plan: json['plan'] as String?,
    approvalStatus: json['approval_status'] as String?,
    videoRetentionDays: (json['video_retention_days'] as num?)?.toInt(),
    features: (json['features'] as List?)?.map((e) => e.toString()).toSet(),
  );
}

/// Feature keys of backend/domain/plan_entitlements.py `FEATURES`.
abstract final class PlanFeature {
  static const comicVideo = 'comic_video';
  static const novelImport = 'novel_import';
  static const socialPublishing = 'social_publishing';
  static const imageTranslation = 'image_translation';
}
