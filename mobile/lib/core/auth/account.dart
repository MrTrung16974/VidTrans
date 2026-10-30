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
  });

  final String userId;
  final String email;
  final String role;
  final String? displayName;
  final String? plan;
  final String? approvalStatus;
  final int? videoRetentionDays;

  /// Viewers may only send read (GET) requests.
  bool get isReadOnly => role == 'viewer';

  String get name => (displayName?.isNotEmpty ?? false) ? displayName! : email;

  factory Account.fromJson(Map<String, dynamic> json) => Account(
        userId: json['user_id'].toString(),
        email: json['email'] as String? ?? '',
        role: json['role'] as String? ?? 'viewer',
        displayName: json['display_name'] as String?,
        plan: json['plan'] as String?,
        approvalStatus: json['approval_status'] as String?,
        videoRetentionDays: (json['video_retention_days'] as num?)?.toInt(),
      );
}
