import 'dart:math' as math;
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/account.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../../jobs/presentation/providers/jobs_providers.dart';
import '../../../profile/presentation/screens/billing_screen.dart';
import '../../data/menu_repository.dart';
import '../../domain/app_menu.dart';

/// Floating Netflix-style glassmorphic pill navigation shell matching
/// `web/assets/styles.css` (`@media (max-width: 1024px) .nav-tabs`) and
/// the mobile `vidtrans.cloud/#create` studio layout.
class HomeShell extends ConsumerStatefulWidget {
  const HomeShell({super.key, required this.shell});

  final StatefulNavigationShell shell;

  @override
  ConsumerState<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends ConsumerState<HomeShell> {
  // Admins change menus in the web admin; pick the change up when the app
  // comes back to the foreground instead of waiting for the next sign-in.
  late final AppLifecycleListener _lifecycle;

  StatefulNavigationShell get shell => widget.shell;

  @override
  void initState() {
    super.initState();
    _lifecycle = AppLifecycleListener(
      onResume: () => ref.invalidate(mobileMenuProvider),
    );
  }

  @override
  void dispose() {
    _lifecycle.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final jobsAsync = ref.watch(jobListProvider);
    final activeCount = jobsAsync.maybeWhen(
      data: (list) => list.activeCount,
      orElse: () => 0,
    );

    final menus = ref.watch(mobileMenuProvider).valueOrNull;
    final items = menus != null && menus.items.isNotEmpty
        ? _dockFromMenus(menus, activeCount)
        : _fallbackDock(ref.watch(sessionProvider).valueOrNull, activeCount);
    final currentDockIndex = items.indexWhere(
      (item) => item.branch == shell.currentIndex,
    );

    final bottomInset = MediaQuery.paddingOf(context).bottom;
    final screenHeight = MediaQuery.sizeOf(context).height;
    final isCompactHeight = screenHeight < 700;
    final bottomMargin = math.max(
      isCompactHeight ? 6.0 : 10.0,
      bottomInset + 2.0,
    );
    final keyboardOpen = isStudioKeyboardOpen(context);

    return Scaffold(
      // Let each branch screen's own Scaffold handle keyboard insets directly
      // so nested screens still receive MediaQuery.viewInsetsOf(context).bottom
      // and don't double-offset or misdetect keyboard state.
      resizeToAvoidBottomInset: false,
      extendBody: !keyboardOpen,
      backgroundColor: AppColors.bg,
      body: shell,
      bottomNavigationBar: keyboardOpen
          ? null
          : Padding(
              padding: EdgeInsets.fromLTRB(
                14,
                isCompactHeight ? 4 : 6,
                14,
                bottomMargin,
              ),
              child: Align(
                alignment: Alignment.bottomCenter,
                heightFactor: 1.0,
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 420),
                  child: _FloatingNetflixDock(
                    items: items,
                    currentIndex: currentDockIndex,
                    compact: isCompactHeight,
                    onSelect: (index) {
                      HapticFeedback.selectionClick();
                      final branch = items[index].branch;
                      if (branch == null) {
                        // Upgrade opens the billing page above the dock.
                        Navigator.of(context).push(
                          MaterialPageRoute<void>(
                            builder: (_) => const BillingScreen(),
                          ),
                        );
                        return;
                      }
                      shell.goBranch(
                        branch,
                        initialLocation: branch == shell.currentIndex,
                      );
                    },
                  ),
                ),
              ),
            ),
    );
  }
}

/// Shell branch and icon of each menu key from the `app_menus` table. Branch
/// indexes follow the StatefulShellRoute order in app/router.dart:
/// 0 translate, 1 comic, 2 jobs, 3 social, 4 profile. A null branch pushes a
/// page instead (upgrade opens billing).
const _menuTargets = <String, ({_NavIconType icon, int? branch})>{
  'translate': (icon: _NavIconType.translate, branch: 0),
  'comic': (icon: _NavIconType.comic, branch: 1),
  'jobs': (icon: _NavIconType.jobs, branch: 2),
  'social': (icon: _NavIconType.social, branch: 3),
  'upgrade': (icon: _NavIconType.upgrade, branch: null),
  'profile': (icon: _NavIconType.profile, branch: 4),
};

/// The dock from the server: entries already filtered by role and plan and
/// sorted by the admin; the first `dockSize` known screens are shown.
List<_DockItem> _dockFromMenus(AppMenuConfig menus, int activeCount) {
  final items = <_DockItem>[];
  for (final menu in menus.items) {
    final target = _menuTargets[menu.key];
    if (target == null) continue; // a screen this app version does not have
    items.add(
      _DockItem(
        type: target.icon,
        label: menu.shortLabel,
        branch: target.branch,
        badgeCount: menu.key == 'jobs' ? activeCount : 0,
      ),
    );
    if (items.length >= menus.dockSize) break;
  }
  return items;
}

/// Built-in dock used until the server menus load (or if they fail): the
/// same defaults the backend seeds into `app_menus`.
List<_DockItem> _fallbackDock(Account? account, int activeCount) {
  final items = <_DockItem>[
    const _DockItem(type: _NavIconType.translate, label: 'Dịch', branch: 0),
    if (account?.canUseComics ?? true)
      const _DockItem(type: _NavIconType.comic, label: 'Truyện', branch: 1),
    _DockItem(
      type: _NavIconType.jobs,
      label: 'Tiến trình',
      branch: 2,
      badgeCount: activeCount,
    ),
    if (account?.canPublishSocial ?? true)
      const _DockItem(type: _NavIconType.social, label: 'Đăng bài', branch: 3),
  ];
  const fillers = <_DockItem>[
    _DockItem(type: _NavIconType.upgrade, label: 'Nâng cấp'),
    _DockItem(type: _NavIconType.profile, label: 'Tài khoản', branch: 4),
  ];
  for (final filler in fillers) {
    if (items.length >= 4) break;
    items.add(filler);
  }
  return items;
}

enum _NavIconType { translate, comic, jobs, social, upgrade, profile }

class _DockItem {
  const _DockItem({
    required this.type,
    required this.label,
    this.branch,
    this.badgeCount = 0,
  });

  final _NavIconType type;
  final String label;

  /// Shell branch the tab opens; null for tabs that push a page instead.
  final int? branch;
  final int badgeCount;
}

class _FloatingNetflixDock extends StatelessWidget {
  const _FloatingNetflixDock({
    required this.items,
    required this.currentIndex,
    required this.onSelect,
    this.compact = false,
  });

  final List<_DockItem> items;
  final int currentIndex;
  final ValueChanged<int> onSelect;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final hasActiveDockTab = currentIndex >= 0 && currentIndex < items.length;

    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(999),
        boxShadow: const [
          BoxShadow(
            color: Color(0x220F172A),
            blurRadius: 28,
            spreadRadius: -2,
            offset: Offset(0, 12),
          ),
          BoxShadow(
            color: Color(0x14EA580C),
            blurRadius: 12,
            offset: Offset(0, 4),
          ),
        ],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(999),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 22, sigmaY: 22),
          child: Container(
            height: compact ? 60 : 64,
            padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 5),
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  Color(0x8FFFFFFF),
                  Color(0x9EFFFAF5),
                ],
              ),
              borderRadius: BorderRadius.circular(999),
              border: Border.all(
                color: const Color(0xB3E2E8F0),
                width: 1.5,
              ),
            ),
            child: LayoutBuilder(
              builder: (context, constraints) {
                final tabWidth = constraints.maxWidth / items.length;
                return Stack(
                  children: [
                    if (hasActiveDockTab)
                      AnimatedPositioned(
                        duration: const Duration(milliseconds: 280),
                        curve: Curves.easeOutCubic,
                        left: currentIndex * tabWidth,
                        top: 0,
                        bottom: 0,
                        width: tabWidth,
                        child: Container(
                          decoration: BoxDecoration(
                            gradient: const LinearGradient(
                              begin: Alignment.topCenter,
                              end: Alignment.bottomCenter,
                              colors: [
                                Color(0xD9FFFAF5),
                                Color(0xE0FFEDD5),
                              ],
                            ),
                            borderRadius: BorderRadius.circular(999),
                            border: Border.all(
                              color: AppColors.brand,
                              width: 1.5,
                            ),
                            boxShadow: const [
                              BoxShadow(
                                color: Color(0x26EA580C),
                                blurRadius: 10,
                                offset: Offset(0, 2),
                              ),
                            ],
                          ),
                        ),
                      ),
                    Row(
                      children: List.generate(items.length, (i) {
                        final item = items[i];
                        final selected = currentIndex == i;
                        return Expanded(
                          child: _DockButton(
                            item: item,
                            selected: selected,
                            onTap: () => onSelect(i),
                          ),
                        );
                      }),
                    ),
                  ],
                );
              },
            ),
          ),
        ),
      ),
    );
  }
}

class _DockButton extends StatelessWidget {
  const _DockButton({
    required this.item,
    required this.selected,
    required this.onTap,
  });

  final _DockItem item;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final fgColor = selected ? AppColors.brand : const Color(0xFF64748B);
    final labelColor = selected ? AppColors.brandDark : const Color(0xFF64748B);

    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: Center(
        child: AnimatedScale(
          duration: const Duration(milliseconds: 220),
          curve: Curves.easeOutBack,
          scale: selected ? 1.04 : 1.0,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Stack(
                clipBehavior: Clip.none,
                children: [
                  SizedBox(
                    width: 22,
                    height: 22,
                    child: CustomPaint(
                      painter: _DockIconPainter(
                        type: item.type,
                        color: fgColor,
                        selected: selected,
                      ),
                    ),
                  ),
                  if (item.badgeCount > 0)
                    Positioned(
                      right: -10,
                      top: -5,
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 5,
                          vertical: 1,
                        ),
                        decoration: BoxDecoration(
                          color: selected
                              ? AppColors.brand
                              : const Color(0xFF0F172A),
                          borderRadius: BorderRadius.circular(999),
                          border: Border.all(
                            color: Colors.white,
                            width: 1.5,
                          ),
                        ),
                        child: Text(
                          '${item.badgeCount}',
                          style: const TextStyle(
                            fontSize: 9,
                            fontWeight: FontWeight.w900,
                            color: Colors.white,
                          ),
                        ),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 4),
              AnimatedDefaultTextStyle(
                duration: const Duration(milliseconds: 200),
                style: TextStyle(
                  fontSize: 11.5,
                  fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
                  color: labelColor,
                  letterSpacing: -0.15,
                  height: 1.1,
                ),
                child: Text(
                  item.label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Custom vector icons matching the exact SVG paths in `web/index.html` `.nav-tabs`:
/// - `translate`: `<path d="M12 5v14M5 12h14"/>`
/// - `comic`: `<path d="M4 5h7v14H4zM13 5h7v6h-7zM13 13h7v6h-7z"/>`
/// - `jobs`: `<path d="M5 4h14v16H5zM8 8h8M8 12h8M8 16h5"/>`
/// - `social`: `<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>`
class _DockIconPainter extends CustomPainter {
  const _DockIconPainter({
    required this.type,
    required this.color,
    required this.selected,
  });

  final _NavIconType type;
  final Color color;
  final bool selected;

  @override
  void paint(Canvas canvas, Size size) {
    final s = size.width / 24.0;
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = (selected ? 2.15 : 1.85) * s
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;

    switch (type) {
      case _NavIconType.translate:
        canvas.drawLine(Offset(12 * s, 5 * s), Offset(12 * s, 19 * s), paint);
        canvas.drawLine(Offset(5 * s, 12 * s), Offset(19 * s, 12 * s), paint);
      case _NavIconType.comic:
        // Left tall panel: M4 5h6.5v14H4z
        canvas.drawRRect(
          RRect.fromRectAndRadius(
            Rect.fromLTWH(4 * s, 4.5 * s, 6.8 * s, 15 * s),
            Radius.circular(1.8 * s),
          ),
          paint,
        );
        // Top-right panel: M13.2 4.5h6.8v6h-6.8z
        canvas.drawRRect(
          RRect.fromRectAndRadius(
            Rect.fromLTWH(13.2 * s, 4.5 * s, 6.8 * s, 6.2 * s),
            Radius.circular(1.8 * s),
          ),
          paint,
        );
        // Bottom-right panel: M13.2 13.3h6.8v6.2h-6.8z
        canvas.drawRRect(
          RRect.fromRectAndRadius(
            Rect.fromLTWH(13.2 * s, 13.3 * s, 6.8 * s, 6.2 * s),
            Radius.circular(1.8 * s),
          ),
          paint,
        );
      case _NavIconType.jobs:
        // Document frame: M5 4h14v16H5z
        canvas.drawRRect(
          RRect.fromRectAndRadius(
            Rect.fromLTWH(5 * s, 3.8 * s, 14 * s, 16.4 * s),
            Radius.circular(2.2 * s),
          ),
          paint,
        );
        // 3 progress lines inside
        canvas.drawLine(
          Offset(8.2 * s, 8.2 * s),
          Offset(15.8 * s, 8.2 * s),
          paint,
        );
        canvas.drawLine(
          Offset(8.2 * s, 12 * s),
          Offset(15.8 * s, 12 * s),
          paint,
        );
        canvas.drawLine(
          Offset(8.2 * s, 15.8 * s),
          Offset(13 * s, 15.8 * s),
          paint,
        );
      case _NavIconType.social:
        // TikTok / music note stem & wave: M11 17.5V5.5c2.2 1.8 4.5 2.4 6.5 2.5
        final stemPath = Path()
          ..moveTo(11.5 * s, 17 * s)
          ..lineTo(11.5 * s, 5 * s)
          ..cubicTo(12.8 * s, 7.2 * s, 15.2 * s, 8.4 * s, 17.8 * s, 8.5 * s);
        canvas.drawPath(stemPath, paint);
        canvas.drawCircle(Offset(8.5 * s, 17 * s), 3.0 * s, paint);
      case _NavIconType.upgrade:
        // Crown: M4 8l4 4 4-6 4 6 4-4-2 10H6z
        final crown = Path()
          ..moveTo(4 * s, 8 * s)
          ..lineTo(8 * s, 12 * s)
          ..lineTo(12 * s, 5.5 * s)
          ..lineTo(16 * s, 12 * s)
          ..lineTo(20 * s, 8 * s)
          ..lineTo(18 * s, 18 * s)
          ..lineTo(6 * s, 18 * s)
          ..close();
        canvas.drawPath(crown, paint);
      case _NavIconType.profile:
        // Head + shoulders: circle(12,8.5,r3.5) + M5 20c0-3.9 3.1-6 7-6s7 2.1 7 6
        canvas.drawCircle(Offset(12 * s, 8.5 * s), 3.5 * s, paint);
        final shoulders = Path()
          ..moveTo(5 * s, 20 * s)
          ..cubicTo(5 * s, 16.1 * s, 8.1 * s, 14 * s, 12 * s, 14 * s)
          ..cubicTo(15.9 * s, 14 * s, 19 * s, 16.1 * s, 19 * s, 20 * s);
        canvas.drawPath(shoulders, paint);
    }
  }

  @override
  bool shouldRepaint(covariant _DockIconPainter oldDelegate) =>
      oldDelegate.type != type ||
      oldDelegate.color != color ||
      oldDelegate.selected != selected;
}

/// Netflix-style category pills displayed directly under the `StudioTopBar`
/// brand row (`[Dịch Video]  [Dịch Ảnh · MỚI]  [Dịch Nổi]  [Công cụ ▾]`).
enum StudioCategoryPill { video, image, live }

/// Global Studio Top Header Bar matching `.topbar` in `web/index.html` & `web/assets/styles.css`:
/// - Left: `[V] VidTrans` brand lockup
/// - Right: circular system status indicator + circular user avatar opening the Studio User Menu
/// - Optional Netflix-style category pill bar (`activeStudioPill`)
/// - Bottom: `2.5px` orange gradient accent line
class StudioTopBar extends ConsumerWidget implements PreferredSizeWidget {
  const StudioTopBar({
    super.key,
    this.bottom,
    this.extraActions,
    this.activeStudioPill,
    this.onSelectStudioPill,
  });

  final PreferredSizeWidget? bottom;
  final List<Widget>? extraActions;
  final StudioCategoryPill? activeStudioPill;
  final ValueChanged<StudioCategoryPill>? onSelectStudioPill;

  double get _categoryBarHeight => activeStudioPill != null ? 46.0 : 0.0;

  @override
  Size get preferredSize => Size.fromHeight(
    60 + _categoryBarHeight + (bottom?.preferredSize.height ?? 0),
  );

  void _handleSelectPill(BuildContext context, StudioCategoryPill pill) {
    HapticFeedback.selectionClick();
    if (onSelectStudioPill != null) {
      onSelectStudioPill!(pill);
      return;
    }
    final router = GoRouter.maybeOf(context);
    if (router == null) return;
    switch (pill) {
      case StudioCategoryPill.video:
        router.go('/translate');
      case StudioCategoryPill.image:
        router.go('/translate/image');
      case StudioCategoryPill.live:
        router.go('/translate/image?mode=live');
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final jobsAsync = ref.watch(jobListProvider);
    final activeCount = jobsAsync.maybeWhen(
      data: (list) => list.activeCount,
      orElse: () => 0,
    );
    final account = ref.watch(sessionProvider).valueOrNull;

    return Material(
      color: Colors.white,
      child: SafeArea(
        bottom: false,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            SizedBox(
              height: 57.5,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 14),
                child: Row(
                  children: [
                    InkWell(
                      onTap: () => GoRouter.maybeOf(context)?.go('/translate'),
                      borderRadius: BorderRadius.circular(10),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 2,
                          vertical: 4,
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            const VidTransLogo(size: 32),
                            const SizedBox(width: 9),
                            Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                const Text(
                                  'VidTrans',
                                  style: TextStyle(
                                    fontSize: 16.5,
                                    fontWeight: FontWeight.w900,
                                    letterSpacing: -0.45,
                                    color: AppColors.ink,
                                    height: 1.05,
                                  ),
                                ),
                                if (MediaQuery.sizeOf(context).width >= 350)
                                  const Text(
                                    'CREATOR STUDIO',
                                    style: TextStyle(
                                      fontSize: 8.5,
                                      fontWeight: FontWeight.w800,
                                      letterSpacing: 0.75,
                                      color: AppColors.muted,
                                      height: 1.15,
                                    ),
                                  ),
                              ],
                            ),
                          ],
                        ),
                      ),
                    ),
                    const Spacer(),
                    if (extraActions != null) ...extraActions!,
                    _SystemStateCircle(
                      activeCount: activeCount,
                      onTap: () {
                        HapticFeedback.lightImpact();
                        GoRouter.maybeOf(context)?.go('/jobs');
                      },
                    ),
                    const SizedBox(width: 10),
                    _UserAvatarCircle(
                      name: account?.name ?? 'Studio',
                      onTap: () {
                        HapticFeedback.lightImpact();
                        _showStudioUserMenu(context, ref);
                      },
                    ),
                  ],
                ),
              ),
            ),
            if (activeStudioPill != null)
              _StudioNetflixPillBar(
                activePill: activeStudioPill!,
                canTranslateImages:
                    account?.hasFeature(PlanFeature.imageTranslation) ?? true,
                canUseFloating:
                    account?.hasFeature(PlanFeature.screenTranslator) ?? true,
                onSelectPill: (pill) => _handleSelectPill(context, pill),
                onOpenAllTools: () {
                  HapticFeedback.lightImpact();
                  _showStudioCategorySheet(context, ref, activeStudioPill!);
                },
              ),
            ?bottom,
            // Crisp 2.5px orange gradient accent line matching `.topbar::after`
            Container(
              height: 2.5,
              decoration: const BoxDecoration(
                gradient: LinearGradient(
                  colors: [
                    Color(0xFFFB923C),
                    Color(0xFFEA580C),
                    Color(0xFFF97316),
                    Color(0xFFFB923C),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  void _showStudioCategorySheet(
    BuildContext context,
    WidgetRef ref,
    StudioCategoryPill currentPill,
  ) {
    final account = ref.read(sessionProvider).valueOrNull;
    final canTranslateImages =
        account?.hasFeature(PlanFeature.imageTranslation) ?? true;
    final canUseFloating =
        account?.hasFeature(PlanFeature.screenTranslator) ?? true;
    final canUseComics = account?.canUseComics ?? true;
    final canPublishSocial = account?.canPublishSocial ?? true;

    showModalBottomSheet<void>(
      context: context,
      useRootNavigator: true,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (sheetContext) {
        return Container(
          margin: const EdgeInsets.fromLTRB(12, 0, 12, 16),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(24),
            border: Border.all(color: AppColors.brandBorder),
            boxShadow: const [
              BoxShadow(
                color: Color(0x2E0F172A),
                blurRadius: 34,
                offset: Offset(0, 14),
              ),
            ],
          ),
          child: SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Center(
                    child: Container(
                      width: 38,
                      height: 4,
                      decoration: BoxDecoration(
                        color: const Color(0xFFCBD5E1),
                        borderRadius: BorderRadius.circular(999),
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  Row(
                    children: [
                      Container(
                        width: 34,
                        height: 34,
                        decoration: BoxDecoration(
                          gradient: const LinearGradient(
                            colors: [Color(0xFFEA580C), Color(0xFFF97316)],
                          ),
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: const Icon(
                          Icons.grid_view_rounded,
                          size: 18,
                          color: Colors.white,
                        ),
                      ),
                      const SizedBox(width: 10),
                      const Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              'Danh mục công cụ VidTrans',
                              style: TextStyle(
                                fontSize: 15.5,
                                fontWeight: FontWeight.w800,
                                color: AppColors.ink,
                              ),
                            ),
                            Text(
                              'Chọn nhanh chế độ dịch và công cụ sáng tạo',
                              style: TextStyle(
                                fontSize: 12,
                                color: AppColors.muted,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  _UserMenuTile(
                    icon: Icons.movie_filter_rounded,
                    title: 'Dịch Video Hàng Loạt (Batch Studio)',
                    subtitle:
                        'Dịch, lồng tiếng AI & ghép phụ đề tới 50 video/lô',
                    selected: currentPill == StudioCategoryPill.video,
                    onTap: () {
                      Navigator.of(sheetContext).pop();
                      _handleSelectPill(context, StudioCategoryPill.video);
                    },
                  ),
                  if (canTranslateImages)
                    _UserMenuTile(
                      icon: Icons.image_search_rounded,
                      title: 'Dịch Ảnh Chụp Màn Hình',
                      subtitle:
                          'Quét OCR chữ Trung, Anh… và thay bằng tiếng Việt ngay trên ảnh',
                      badge: 'MỚI',
                      selected: currentPill == StudioCategoryPill.image,
                      onTap: () {
                        Navigator.of(sheetContext).pop();
                        _handleSelectPill(context, StudioCategoryPill.image);
                      },
                    ),
                  if (canUseFloating)
                    _UserMenuTile(
                      icon: Icons.touch_app_rounded,
                      title: 'Nút Dịch Nổi Trực Tiếp (Live Overlay)',
                      subtitle:
                          'Bật nút dịch nổi trên WeChat, Zalo, trình duyệt, app đọc truyện',
                      badge: 'HOT',
                      selected: currentPill == StudioCategoryPill.live,
                      onTap: () {
                        Navigator.of(sheetContext).pop();
                        _handleSelectPill(context, StudioCategoryPill.live);
                      },
                    ),
                  if (canUseComics)
                    _UserMenuTile(
                      icon: Icons.auto_stories_rounded,
                      title: 'Truyện Chữ → Video Hoạt Hình AI',
                      subtitle:
                          'Chuyển tiểu thuyết, truyện chữ thành tập phim AI tự động',
                      onTap: () {
                        Navigator.of(sheetContext).pop();
                        GoRouter.maybeOf(context)?.go('/comic');
                      },
                    ),
                  _UserMenuTile(
                    icon: Icons.bolt_rounded,
                    title: 'Tiến Trình & Tải Video',
                    subtitle: 'Theo dõi hàng đợi xử lý và tải kết quả về máy',
                    onTap: () {
                      Navigator.of(sheetContext).pop();
                      GoRouter.maybeOf(context)?.go('/jobs');
                    },
                  ),
                  if (canPublishSocial)
                    _UserMenuTile(
                      icon: Icons.share_rounded,
                      title: 'Đăng Bài Mạng Xã Hội',
                      subtitle:
                          'Lên lịch và đăng video lên TikTok, YouTube, Facebook',
                      onTap: () {
                        Navigator.of(sheetContext).pop();
                        GoRouter.maybeOf(context)?.go('/social');
                      },
                    ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }

  void _showStudioUserMenu(BuildContext context, WidgetRef ref) {
    final account = ref.read(sessionProvider).valueOrNull;
    showModalBottomSheet<void>(
      context: context,
      useRootNavigator: true,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (sheetContext) {
        return Container(
          margin: const EdgeInsets.fromLTRB(12, 0, 12, 16),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(24),
            border: Border.all(color: AppColors.line),
            boxShadow: const [
              BoxShadow(
                color: Color(0x260F172A),
                blurRadius: 32,
                offset: Offset(0, 12),
              ),
            ],
          ),
          child: SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 14),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Center(
                    child: Container(
                      width: 38,
                      height: 4,
                      decoration: BoxDecoration(
                        color: const Color(0xFFCBD5E1),
                        borderRadius: BorderRadius.circular(999),
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  if (account != null)
                    Container(
                      padding: const EdgeInsets.all(14),
                      decoration: BoxDecoration(
                        gradient: AppColors.studioHeroGradient,
                        borderRadius: BorderRadius.circular(16),
                        border: Border.all(color: AppColors.brandBorder),
                      ),
                      child: Row(
                        children: [
                          _UserAvatarCircle(name: account.name, size: 46),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  account.name,
                                  style: const TextStyle(
                                    fontSize: 16,
                                    fontWeight: FontWeight.w800,
                                    color: AppColors.ink,
                                  ),
                                ),
                                const SizedBox(height: 2),
                                Text(
                                  account.email,
                                  style: const TextStyle(
                                    fontSize: 12.5,
                                    color: AppColors.muted,
                                  ),
                                ),
                              ],
                            ),
                          ),
                          StudioStatusPill(
                            status: 'active',
                            label: account.role.toUpperCase(),
                            compact: true,
                          ),
                        ],
                      ),
                    ),
                  const SizedBox(height: 10),
                  _UserMenuTile(
                    icon: Icons.person_outline_rounded,
                    title: 'Tài khoản & gắn logo',
                    subtitle: 'Hồ sơ cá nhân, quyền hạn và quản lý tài khoản',
                    onTap: () {
                      Navigator.of(sheetContext).pop();
                      context.go('/profile');
                    },
                  ),
                  if (account != null && !account.isReadOnly)
                    _UserMenuTile(
                      icon: Icons.branding_watermark_outlined,
                      title: 'Cấu hình gắn logo thương hiệu',
                      subtitle:
                          'Tải lên logo và chọn góc hiển thị khi chèn vào video',
                      onTap: () {
                        Navigator.of(sheetContext).pop();
                        context.push('/branding');
                      },
                    ),
                  _UserMenuTile(
                    icon: Icons.workspace_premium_outlined,
                    title: 'Gói dịch vụ & thanh toán',
                    subtitle:
                        'Xem hạn mức sử dụng, nâng cấp gói và lịch sử đơn',
                    onTap: () {
                      Navigator.of(sheetContext).pop();
                      Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) => const BillingScreen(),
                        ),
                      );
                    },
                  ),
                  const Divider(height: 16),
                  _UserMenuTile(
                    icon: Icons.logout_rounded,
                    title: 'Đăng xuất',
                    subtitle: 'Thoát phiên làm việc trên thiết bị này',
                    danger: true,
                    onTap: () {
                      Navigator.of(sheetContext).pop();
                      ref.read(sessionProvider.notifier).logout();
                    },
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

/// Horizontal Netflix-style pill menu bar (`[Dịch Video] [Dịch Ảnh · MỚI] [Dịch Nổi] [Công cụ ▾]`)
/// styled in VidTrans Studio's warm orange, cream, and slate color palette.
class _StudioNetflixPillBar extends StatefulWidget {
  const _StudioNetflixPillBar({
    required this.activePill,
    required this.canTranslateImages,
    required this.canUseFloating,
    required this.onSelectPill,
    required this.onOpenAllTools,
  });

  final StudioCategoryPill activePill;
  final bool canTranslateImages;
  final bool canUseFloating;
  final ValueChanged<StudioCategoryPill> onSelectPill;
  final VoidCallback onOpenAllTools;

  @override
  State<_StudioNetflixPillBar> createState() => _StudioNetflixPillBarState();
}

class _StudioNetflixPillBarState extends State<_StudioNetflixPillBar> {
  final _videoKey = GlobalKey();
  final _imageKey = GlobalKey();
  final _liveKey = GlobalKey();
  final _toolsKey = GlobalKey();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToActive());
  }

  @override
  void didUpdateWidget(covariant _StudioNetflixPillBar oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.activePill != widget.activePill) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToActive());
    }
  }

  void _scrollToActive() {
    if (!mounted) return;
    // When "Dịch Nổi" is active, ensure the rightmost "Công cụ" pill is also
    // brought into view so it is never clipped off the right edge.
    final targetKey = switch (widget.activePill) {
      StudioCategoryPill.video => _videoKey,
      StudioCategoryPill.image => _imageKey,
      StudioCategoryPill.live => _toolsKey,
    };
    final ctx = targetKey.currentContext ?? _liveKey.currentContext;
    if (ctx != null) {
      Scrollable.ensureVisible(
        ctx,
        duration: const Duration(milliseconds: 240),
        curve: Curves.easeOutCubic,
        alignment: widget.activePill == StudioCategoryPill.live ? 1.0 : 0.3,
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final compact = MediaQuery.sizeOf(context).width < 395;
    final gap = compact ? 6.0 : 8.0;
    return SizedBox(
      height: 46,
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        padding: EdgeInsets.fromLTRB(compact ? 12 : 14, 2, compact ? 12 : 14, 10),
        child: Row(
          children: [
            KeyedSubtree(
              key: _videoKey,
              child: _NetflixPillButton(
                icon: Icons.movie_filter_rounded,
                label: 'Dịch Video',
                compact: compact,
                selected: widget.activePill == StudioCategoryPill.video,
                onTap: () => widget.onSelectPill(StudioCategoryPill.video),
              ),
            ),
            if (widget.canTranslateImages) ...[
              SizedBox(width: gap),
              KeyedSubtree(
                key: _imageKey,
                child: _NetflixPillButton(
                  icon: Icons.image_search_rounded,
                  label: 'Dịch Ảnh',
                  badge: 'MỚI',
                  compact: compact,
                  selected: widget.activePill == StudioCategoryPill.image,
                  onTap: () => widget.onSelectPill(StudioCategoryPill.image),
                ),
              ),
            ],
            if (widget.canUseFloating) ...[
              SizedBox(width: gap),
              KeyedSubtree(
                key: _liveKey,
                child: _NetflixPillButton(
                  icon: Icons.touch_app_rounded,
                  label: 'Dịch Nổi',
                  compact: compact,
                  selected: widget.activePill == StudioCategoryPill.live,
                  onTap: () => widget.onSelectPill(StudioCategoryPill.live),
                ),
              ),
            ],
            SizedBox(width: gap),
            KeyedSubtree(
              key: _toolsKey,
              child: _NetflixPillButton(
                label: 'Công cụ',
                trailingIcon: Icons.keyboard_arrow_down_rounded,
                compact: compact,
                selected: false,
                onTap: widget.onOpenAllTools,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _NetflixPillButton extends StatelessWidget {
  const _NetflixPillButton({
    required this.label,
    required this.selected,
    required this.onTap,
    this.icon,
    this.badge,
    this.trailingIcon,
    this.compact = false,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;
  final IconData? icon;
  final String? badge;
  final IconData? trailingIcon;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final isHighlightedUnselected = badge != null && !selected;
    final fgColor = selected
        ? Colors.white
        : (isHighlightedUnselected
              ? const Color(0xFF9A3412)
              : const Color(0xFF1E293B));
    final iconColor = selected ? Colors.white : AppColors.brand;

    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(999),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOutCubic,
          height: 34,
          padding: EdgeInsets.symmetric(horizontal: compact ? 10.5 : 13),
          decoration: BoxDecoration(
            gradient: selected
                ? const LinearGradient(
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                    colors: [Color(0xFFEA580C), Color(0xFFF97316)],
                  )
                : isHighlightedUnselected
                ? const LinearGradient(
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                    colors: [Color(0xFFFFF7ED), Color(0xFFFFEDD5)],
                  )
                : const LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [Color(0xFFFFFFFF), Color(0xFFF8FAFC)],
                  ),
            borderRadius: BorderRadius.circular(999),
            border: Border.all(
              color: selected
                  ? const Color(0xFFFB923C)
                  : isHighlightedUnselected
                  ? const Color(0xFFFDBA74)
                  : const Color(0xFFCBD5E1),
              width: 1.2,
            ),
            boxShadow: selected
                ? const [
                    BoxShadow(
                      color: Color(0x38EA580C),
                      blurRadius: 10,
                      offset: Offset(0, 3),
                    ),
                  ]
                : const [
                    BoxShadow(
                      color: Color(0x0A0F172A),
                      blurRadius: 4,
                      offset: Offset(0, 1),
                    ),
                  ],
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (icon != null) ...[
                Icon(icon, size: 15.5, color: iconColor),
                const SizedBox(width: 5.5),
              ],
              Text(
                label,
                style: TextStyle(
                  fontSize: 12.5,
                  fontWeight: selected ? FontWeight.w800 : FontWeight.w700,
                  color: fgColor,
                  letterSpacing: -0.2,
                ),
              ),
              if (badge != null) ...[
                const SizedBox(width: 5),
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 5.5,
                    vertical: 1.5,
                  ),
                  decoration: BoxDecoration(
                    color: selected
                        ? Colors.white.withValues(alpha: 0.24)
                        : AppColors.brand,
                    borderRadius: BorderRadius.circular(999),
                  ),
                  child: Text(
                    badge!,
                    style: const TextStyle(
                      fontSize: 9,
                      fontWeight: FontWeight.w900,
                      color: Colors.white,
                      letterSpacing: 0.2,
                      height: 1.0,
                    ),
                  ),
                ),
              ],
              if (trailingIcon != null) ...[
                const SizedBox(width: 2),
                Icon(trailingIcon, size: 17, color: fgColor),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _UserMenuTile extends StatelessWidget {
  const _UserMenuTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.onTap,
    this.danger = false,
    this.selected = false,
    this.badge,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback onTap;
  final bool danger;
  final bool selected;
  final String? badge;

  @override
  Widget build(BuildContext context) {
    final color = danger
        ? AppColors.red
        : (selected ? AppColors.brandDark : AppColors.ink);
    final iconBg = danger
        ? AppColors.redSoft
        : (selected ? AppColors.brand : AppColors.brandSoft);
    final iconColor = danger
        ? AppColors.red
        : (selected ? Colors.white : AppColors.brand);

    return Material(
      color: selected ? AppColors.brandTint : Colors.transparent,
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(14),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
          decoration: selected
              ? BoxDecoration(
                  borderRadius: BorderRadius.circular(14),
                  border: Border.all(color: AppColors.brandBorder),
                )
              : null,
          child: Row(
            children: [
              Container(
                width: 38,
                height: 38,
                decoration: BoxDecoration(
                  color: iconBg,
                  borderRadius: BorderRadius.circular(11),
                ),
                alignment: Alignment.center,
                child: Icon(icon, size: 20, color: iconColor),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Flexible(
                          child: Text(
                            title,
                            style: TextStyle(
                              fontSize: 14,
                              fontWeight: FontWeight.w700,
                              color: color,
                            ),
                          ),
                        ),
                        if (badge != null) ...[
                          const SizedBox(width: 6),
                          StudioStatusPill(
                            label: badge!,
                            compact: true,
                            showDot: false,
                          ),
                        ],
                      ],
                    ),
                    const SizedBox(height: 1),
                    Text(
                      subtitle,
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.muted,
                      ),
                    ),
                  ],
                ),
              ),
              Icon(
                selected ? Icons.check_circle_rounded : Icons.chevron_right_rounded,
                size: 20,
                color: danger
                    ? AppColors.red
                    : (selected ? AppColors.brand : AppColors.muted),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Circular system status button matching `.system-state` in the mobile screenshot.
class _SystemStateCircle extends StatefulWidget {
  const _SystemStateCircle({required this.activeCount, required this.onTap});

  final int activeCount;
  final VoidCallback onTap;

  @override
  State<_SystemStateCircle> createState() => _SystemStateCircleState();
}

class _SystemStateCircleState extends State<_SystemStateCircle>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1400),
    );
    if (widget.activeCount > 0) {
      _controller.repeat();
    }
  }

  @override
  void didUpdateWidget(covariant _SystemStateCircle oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.activeCount > 0 && !_controller.isAnimating) {
      _controller.repeat();
    } else if (widget.activeCount == 0 && _controller.isAnimating) {
      _controller.stop();
      _controller.value = 0;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // Idle shows a check: a still spinner arc read as a load that hung.
    final busy = widget.activeCount > 0;
    return Tooltip(
      message: busy
          ? '${widget.activeCount} tiến trình đang xử lý'
          : 'Hệ thống sẵn sàng',
      child: InkWell(
        onTap: widget.onTap,
        customBorder: const CircleBorder(),
        child: Stack(
          clipBehavior: Clip.none,
          children: [
            AnimatedContainer(
              duration: const Duration(milliseconds: 250),
              width: 34,
              height: 34,
              decoration: BoxDecoration(
                color: busy ? AppColors.brandTint : AppColors.greenTint,
                shape: BoxShape.circle,
                border: Border.all(
                  color: busy ? AppColors.brandBorder : AppColors.greenBorder,
                  width: 1.2,
                ),
                boxShadow: [
                  BoxShadow(
                    color: (busy ? AppColors.brand : AppColors.green)
                        .withValues(alpha: 0.08),
                    blurRadius: 6,
                    offset: const Offset(0, 2),
                  ),
                ],
              ),
              alignment: Alignment.center,
              child: AnimatedSwitcher(
                duration: const Duration(milliseconds: 250),
                child: busy
                    ? RotationTransition(
                        key: const ValueKey('busy'),
                        turns: _controller,
                        child: const SizedBox(
                          width: 15,
                          height: 15,
                          child: CustomPaint(painter: _SystemArcPainter()),
                        ),
                      )
                    : const Icon(
                        Icons.check_rounded,
                        key: ValueKey('idle'),
                        size: 18,
                        color: AppColors.greenDark,
                      ),
              ),
            ),
            if (widget.activeCount > 0)
              Positioned(
                right: -3,
                top: -3,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 4.5,
                    vertical: 0.5,
                  ),
                  decoration: BoxDecoration(
                    color: AppColors.brand,
                    borderRadius: BorderRadius.circular(999),
                    border: Border.all(color: Colors.white, width: 1.2),
                  ),
                  child: Text(
                    '${widget.activeCount}',
                    style: const TextStyle(
                      fontSize: 8.5,
                      fontWeight: FontWeight.w800,
                      color: Colors.white,
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _SystemArcPainter extends CustomPainter {
  const _SystemArcPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    final paint = Paint()
      ..color = AppColors.brand
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.2
      ..strokeCap = StrokeCap.round;

    canvas.drawArc(rect.deflate(1.2), 0.35, math.pi * 1.38, false, paint);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

/// Circular studio avatar badge matching `#userAvatar` in the top-right of the screenshot.
class _UserAvatarCircle extends StatelessWidget {
  const _UserAvatarCircle({required this.name, this.size = 36, this.onTap});

  final String name;
  final double size;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final trimmed = name.trim();
    final initials = trimmed.isEmpty
        ? 'VT'
        : trimmed
              .split(RegExp(r'\s+'))
              .take(2)
              .map((part) => part.characters.first.toUpperCase())
              .join();

    final badge = Stack(
      clipBehavior: Clip.none,
      children: [
        Container(
          width: size,
          height: size,
          padding: const EdgeInsets.all(2),
          decoration: BoxDecoration(
            color: Colors.white,
            shape: BoxShape.circle,
            border: Border.all(color: const Color(0xFFCBD5E1), width: 1.2),
            boxShadow: const [
              BoxShadow(
                color: Color(0x180F172A),
                blurRadius: 8,
                offset: Offset(0, 2),
              ),
            ],
          ),
          child: Container(
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: const RadialGradient(
                center: Alignment(-0.2, -0.3),
                radius: 1.0,
                colors: [
                  Color(0xFFDC2626),
                  Color(0xFF7F1D1D),
                  Color(0xFF1E1B4B),
                ],
                stops: [0.0, 0.55, 1.0],
              ),
              border: Border.all(color: const Color(0xFFF59E0B), width: 1.3),
            ),
            alignment: Alignment.center,
            child: Text(
              initials,
              style: TextStyle(
                color: const Color(0xFFFEF08A),
                fontSize: size * 0.32,
                fontWeight: FontWeight.w900,
                letterSpacing: -0.3,
                shadows: const [
                  Shadow(
                    color: Colors.black54,
                    blurRadius: 3,
                    offset: Offset(0, 1),
                  ),
                ],
              ),
            ),
          ),
        ),
        Positioned(
          right: -1,
          bottom: -1,
          child: Container(
            width: size * 0.32,
            height: size * 0.32,
            decoration: BoxDecoration(
              color: const Color(0xFF0F172A),
              shape: BoxShape.circle,
              border: Border.all(color: Colors.white, width: 1.3),
            ),
            alignment: Alignment.center,
            child: Icon(
              Icons.bolt_rounded,
              size: size * 0.2,
              color: const Color(0xFFFBBF24),
            ),
          ),
        ),
      ],
    );

    if (onTap == null) return badge;
    return InkWell(
      onTap: onTap,
      customBorder: const CircleBorder(),
      child: badge,
    );
  }
}

/// Placeholder for modules scheduled in later phases of the mobile plan.
class ComingSoonScreen extends StatelessWidget {
  const ComingSoonScreen({super.key, required this.title});

  final String title;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: const StudioEmptyState(
        icon: Icons.construction_rounded,
        title: 'Tính năng đang phát triển',
        subtitle: 'Module này sẽ sớm có mặt trong bản cập nhật tiếp theo.',
      ),
    );
  }
}
