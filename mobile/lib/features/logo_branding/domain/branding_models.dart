/// Account branding contract (backend/domain/branding.py, web/js/branding.js).
library;

const logoPositions = ['top-right', 'top-left', 'bottom-right', 'bottom-left'];

const positionLabels = {
  'top-right': 'Góc trên bên phải',
  'top-left': 'Góc trên bên trái',
  'bottom-right': 'Góc dưới bên phải',
  'bottom-left': 'Góc dưới bên trái',
};

/// Placement choices when queueing a video (`override` form field).
const overrideLabels = {
  'auto': 'Tự động theo cấu hình',
  'top-right': 'Ép góc trên bên phải',
  'top-left': 'Ép góc trên bên trái',
  'bottom-right': 'Ép góc dưới bên phải',
  'bottom-left': 'Ép góc dưới bên trái',
};

const multiLogoStrategyLabels = {
  'nearest-default': 'Gần vị trí mặc định nhất',
  'largest': 'Lớn nhất',
  'most-confident': 'Nhận diện chắc chắn nhất',
};

const lowConfidenceActionLabels = {
  'default': 'Dùng vị trí mặc định',
  'review': 'Dùng vị trí mặc định và yêu cầu tôi xác nhận',
};

const coverMethodLabels = {
  'inpaint': 'Khôi phục nền (inpainting, theo được logo di chuyển)',
  'blur': 'Làm mờ vùng logo (nhanh, chỉ logo đứng yên)',
};

/// `PUT /api/v1/me/branding` body. The server forbids extra fields, so
/// [toJson] sends exactly the web form's keys.
class BrandingConfig {
  const BrandingConfig({
    this.autoDetect = true,
    this.defaultPosition = 'top-right',
    this.logoSize = 12,
    this.opacity = 1,
    this.margin = 3,
    this.coverOriginal = false,
    this.coverMethod = 'inpaint',
    this.multiLogoStrategy = 'nearest-default',
    this.lowConfidenceAction = 'default',
    this.minConfidence = 0.55,
  });

  final bool autoDetect;
  final String defaultPosition;

  /// Logo width as % of the video width (3..40).
  final double logoSize;

  /// 0.1..1 (the web shows it as 10..100%).
  final double opacity;

  /// Gap to the edge as % of the shorter side (0..15).
  final double margin;
  final bool coverOriginal;
  final String coverMethod;
  final String multiLogoStrategy;
  final String lowConfidenceAction;

  /// Not editable on the web; preserved as loaded.
  final double minConfidence;

  factory BrandingConfig.fromJson(Map<String, dynamic> json) => BrandingConfig(
        autoDetect: json['auto_detect'] as bool? ?? true,
        defaultPosition: json['default_position'] as String? ?? 'top-right',
        logoSize: (json['logo_size'] as num?)?.toDouble() ?? 12,
        opacity: (json['opacity'] as num?)?.toDouble() ?? 1,
        margin: (json['margin'] as num?)?.toDouble() ?? 3,
        coverOriginal: json['cover_original'] as bool? ?? false,
        coverMethod: json['cover_method'] as String? ?? 'inpaint',
        multiLogoStrategy: json['multi_logo_strategy'] as String? ?? 'nearest-default',
        lowConfidenceAction: json['low_confidence_action'] as String? ?? 'default',
        minConfidence: (json['min_confidence'] as num?)?.toDouble() ?? 0.55,
      );

  Map<String, dynamic> toJson() => {
        'auto_detect': autoDetect,
        'cover_original': coverOriginal,
        'cover_method': coverMethod,
        'default_position': defaultPosition,
        'logo_size': logoSize,
        'opacity': opacity,
        'margin': margin,
        'multi_logo_strategy': multiLogoStrategy,
        'low_confidence_action': lowConfidenceAction,
        'min_confidence': minConfidence,
      };

  BrandingConfig copyWith({
    bool? autoDetect,
    String? defaultPosition,
    double? logoSize,
    double? opacity,
    double? margin,
    bool? coverOriginal,
    String? coverMethod,
    String? multiLogoStrategy,
    String? lowConfidenceAction,
  }) =>
      BrandingConfig(
        autoDetect: autoDetect ?? this.autoDetect,
        defaultPosition: defaultPosition ?? this.defaultPosition,
        logoSize: logoSize ?? this.logoSize,
        opacity: opacity ?? this.opacity,
        margin: margin ?? this.margin,
        coverOriginal: coverOriginal ?? this.coverOriginal,
        coverMethod: coverMethod ?? this.coverMethod,
        multiLogoStrategy: multiLogoStrategy ?? this.multiLogoStrategy,
        lowConfidenceAction: lowConfidenceAction ?? this.lowConfidenceAction,
        minConfidence: minConfidence,
      );
}

/// Stored logo or template image. [url] needs the Bearer header.
class BrandingImage {
  const BrandingImage({required this.url, required this.width, required this.height, this.transparent = false});

  final String url;
  final int width;
  final int height;
  final bool transparent;

  static BrandingImage? fromJson(Object? json) {
    if (json is! Map) return null;
    return BrandingImage(
      url: json['url'] as String,
      width: (json['width'] as num?)?.toInt() ?? 0,
      height: (json['height'] as num?)?.toInt() ?? 0,
      transparent: json['transparent'] == true,
    );
  }
}

/// `GET /api/v1/me/branding`.
class BrandingSettings {
  const BrandingSettings({required this.config, this.logo, this.template});

  final BrandingConfig config;
  final BrandingImage? logo;

  /// Sample of the old logo to find and replace in videos.
  final BrandingImage? template;

  factory BrandingSettings.fromJson(Map<String, dynamic> json) => BrandingSettings(
        config: BrandingConfig.fromJson(Map<String, dynamic>.from(json['config'] as Map? ?? const {})),
        logo: BrandingImage.fromJson(json['logo']),
        template: BrandingImage.fromJson(json['template']),
      );
}

/// A logo job from `/api/v1/branding/videos`.
class BrandingJob {
  const BrandingJob(this.raw);

  final Map<String, dynamic> raw;

  String get id => raw['job_id'].toString();
  String get status => raw['status'] as String? ?? 'queued';
  String get title => (raw['filename'] as String?) ?? id;
  String? get error => raw['error'] as String?;
  String? get stepDetail => raw['step_detail'] as String?;
  DateTime? get createdAt => DateTime.tryParse(raw['created_at']?.toString() ?? '')?.toLocal();

  /// Logo jobs report progress as a 0..1 fraction (branding_service.py).
  double get progress => ((raw['progress'] as num?)?.toDouble() ?? 0).clamp(0, 1);

  Map<String, dynamic>? get _info => raw['branding'] is Map ? Map<String, dynamic>.from(raw['branding'] as Map) : null;

  bool get isActive => const {'queued', 'scheduled', 'processing', 'cancelling'}.contains(status);
  bool get canCancel => status == 'queued' || status == 'processing';
  bool get canReprocess => const {'completed', 'failed', 'cancelled'}.contains(status);
  bool get canDownload => status == 'completed';
  bool get needsReview => _info?['needs_review'] == true;

  /// Same wording as placementText() in web/js/branding.js.
  String? get placementText {
    final info = _info;
    if (info == null) return null;
    final override = info['override'];
    if (override is String && override != 'auto') {
      return 'Ép ${(positionLabels[override] ?? override).toLowerCase()}';
    }
    final detection = info['detection'];
    if (detection is! Map) return 'Không nhận diện · vị trí mặc định';
    final cover = switch (info['cover_method']) {
      'inpaint' => ' · đã khôi phục nền logo cũ',
      'blur' => ' · đã làm mờ logo cũ',
      _ => '',
    };
    final via = detection['method'] == 'template' ? ' theo mẫu' : '';
    final placements = (info['placements'] as List?) ?? const [];
    final moving = placements.any((p) => p is Map && p['source'] == 'tracked') ? ' · logo di chuyển, đã bám theo' : '';
    switch (detection['status']) {
      case 'found':
        final spans = placements.length > 1 ? ' · ${placements.length} đoạn' : '';
        final confidence = (((detection['confidence'] as num?) ?? 0) * 100).round();
        return 'Tìm thấy logo$via (độ tin cậy $confidence%)$spans$moving$cover';
      case 'uncertain':
        return 'Không chắc chắn · dùng vị trí mặc định';
      default:
        return 'Không có logo · vị trí mặc định';
    }
  }
}

class BrandingHistory {
  const BrandingHistory({required this.items, required this.total});

  final List<BrandingJob> items;
  final int total;

  bool get hasActive => items.any((job) => job.isActive);

  factory BrandingHistory.fromJson(Map<String, dynamic> json) => BrandingHistory(
        items: [
          for (final item in (json['items'] as List? ?? const []))
            BrandingJob(Map<String, dynamic>.from(item as Map)),
        ],
        total: (json['total'] as num?)?.toInt() ?? 0,
      );
}

/// Text fields of `POST /api/v1/branding/videos` besides the `file` part
/// (the web form sends `override` and `rights_confirmed=true`).
Map<String, String> brandingVideoFields({required String override, required bool rightsConfirmed}) {
  assert(overrideLabels.containsKey(override));
  return {'override': override, 'rights_confirmed': rightsConfirmed ? 'true' : 'false'};
}
