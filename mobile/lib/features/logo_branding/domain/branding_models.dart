/// Account branding contract (backend/domain/branding.py, web/js/branding.js).
library;

const logoPositions = ['top-right', 'top-left', 'bottom-right', 'bottom-left'];

const positionLabels = {
  'top-right': 'Góc trên bên phải',
  'top-left': 'Góc trên bên trái',
  'bottom-right': 'Góc dưới bên phải',
  'bottom-left': 'Góc dưới bên trái',
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

