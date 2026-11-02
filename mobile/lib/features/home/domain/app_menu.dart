/// One navigation entry from `GET /api/v1/me/menus?platform=mobile`, configured
/// by admins in the `app_menus` table (backend/domain/app_menus.py).
class AppMenu {
  const AppMenu({
    required this.key,
    required this.label,
    required this.shortLabel,
  });

  /// Fixed screen key: translate, comic, jobs, social, upgrade, profile.
  final String key;
  final String label;
  final String shortLabel;

  factory AppMenu.fromJson(Map<String, dynamic> json) {
    final label = json['label']?.toString() ?? '';
    final short = json['short_label']?.toString() ?? '';
    return AppMenu(
      key: json['key'].toString(),
      label: label,
      shortLabel: short.isEmpty ? label : short,
    );
  }
}

/// The menus this account sees, in display order, and how many fit the dock.
class AppMenuConfig {
  const AppMenuConfig({required this.items, required this.dockSize});

  final List<AppMenu> items;
  final int dockSize;

  factory AppMenuConfig.fromJson(Map<String, dynamic> json) => AppMenuConfig(
    items: [
      for (final item in (json['items'] as List? ?? const []))
        if (item is Map) AppMenu.fromJson(Map<String, dynamic>.from(item)),
    ],
    dockSize: (json['dock_size'] as num?)?.toInt() ?? 4,
  );
}
