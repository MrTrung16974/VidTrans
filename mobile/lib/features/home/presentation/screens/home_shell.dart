import 'dart:math' as math;
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../../jobs/presentation/providers/jobs_providers.dart';
import '../../../profile/presentation/screens/billing_screen.dart';

/// Floating Netflix-style glassmorphic pill navigation shell matching
/// `web/assets/styles.css` (`@media (max-width: 1024px) .nav-tabs`) and
/// the mobile `vidtrans.cloud/#create` studio layout.
class HomeShell extends ConsumerWidget {
  const HomeShell({super.key, required this.shell});

  final StatefulNavigationShell shell;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final jobsAsync = ref.watch(jobListProvider);
    final activeCount = jobsAsync.maybeWhen(
      data: (list) => list.jobs.where((j) => j.isActive).length,
      orElse: () => 0,
    );

    final items = <_DockItem>[
      const _DockItem(
        type: _NavIconType.translate,
        label: 'Dịch',
      ),
      const _DockItem(
        type: _NavIconType.comic,
        label: 'Truyện',
      ),
      _DockItem(
        type: _NavIconType.jobs,
        label: 'Tiến trình',
        badgeCount: activeCount,
      ),
      const _DockItem(
        type: _NavIconType.social,
        label: 'Đăng bài',
      ),
    ];

    final bottomInset = MediaQuery.paddingOf(context).bottom;
    final bottomMargin = math.max(12.0, bottomInset + 6.0);

    return Scaffold(
      extendBody: true,
      body: shell,
      bottomNavigationBar: Padding(
        padding: EdgeInsets.fromLTRB(12, 0, 12, bottomMargin),
        child: Align(
          alignment: Alignment.bottomCenter,
          heightFactor: 1.0,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 400),
            child: _FloatingNetflixDock(
              items: items,
              currentIndex: shell.currentIndex,
              onSelect: (index) {
                HapticFeedback.selectionClick();
                shell.goBranch(
                  index,
                  initialLocation: index == shell.currentIndex,
                );
              },
            ),
          ),
        ),
      ),
    );
  }
}

enum _NavIconType {
  translate,
  comic,
  jobs,
  social,
}

class _DockItem {
  const _DockItem({
    required this.type,
    required this.label,
    this.badgeCount = 0,
  });

  final _NavIconType type;
  final String label;
  final int badgeCount;
}

class _FloatingNetflixDock extends StatelessWidget {
  const _FloatingNetflixDock({
    required this.items,
    required this.currentIndex,
    required this.onSelect,
  });

  final List<_DockItem> items;
  final int currentIndex;
  final ValueChanged<int> onSelect;

  @override
  Widget build(BuildContext context) {
    final hasActiveDockTab = currentIndex >= 0 && currentIndex < items.length;

    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(32),
        boxShadow: const [
          BoxShadow(
            color: Color(0x240F172A),
            blurRadius: 30,
            spreadRadius: -2,
            offset: Offset(0, 12),
          ),
          BoxShadow(
            color: Color(0x0D0F172A),
            blurRadius: 8,
            offset: Offset(0, 3),
          ),
        ],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(32),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 28, sigmaY: 28),
          child: Container(
            height: 64,
            padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 4),
            decoration: BoxDecoration(
              color: Colors.white.withValues(alpha: 0.94),
              borderRadius: BorderRadius.circular(32),
              border: Border.all(
                color: const Color(0xFFE2E8F0),
                width: 1.2,
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
                                Color(0xFFFFFBF7),
                                Color(0xFFFFF3E6),
                              ],
                            ),
                            borderRadius: BorderRadius.circular(25),
                            border: Border.all(
                              color: const Color(0x66FB923C),
                              width: 1.15,
                            ),
                            boxShadow: const [
                              BoxShadow(
                                color: Color(0x22EA580C),
                                blurRadius: 8,
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
    final labelColor =
        selected ? const Color(0xFFC2410C) : const Color(0xFF64748B);

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
                          border: Border.all(color: Colors.white, width: 1.2),
                        ),
                        child: Text(
                          '${item.badgeCount}',
                          style: const TextStyle(
                            fontSize: 9,
                            fontWeight: FontWeight.w800,
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
        canvas.drawLine(Offset(8.2 * s, 8.2 * s), Offset(15.8 * s, 8.2 * s), paint);
        canvas.drawLine(Offset(8.2 * s, 12 * s), Offset(15.8 * s, 12 * s), paint);
        canvas.drawLine(Offset(8.2 * s, 15.8 * s), Offset(13 * s, 15.8 * s), paint);
      case _NavIconType.social:
        // TikTok / music note stem & wave: M11 17.5V5.5c2.2 1.8 4.5 2.4 6.5 2.5
        final stemPath = Path()
          ..moveTo(11.5 * s, 17 * s)
          ..lineTo(11.5 * s, 5 * s)
          ..cubicTo(
            12.8 * s,
            7.2 * s,
            15.2 * s,
            8.4 * s,
            17.8 * s,
            8.5 * s,
          );
        canvas.drawPath(stemPath, paint);
        canvas.drawCircle(Offset(8.5 * s, 17 * s), 3.0 * s, paint);
    }
  }

  @override
  bool shouldRepaint(covariant _DockIconPainter oldDelegate) =>
      oldDelegate.type != type ||
      oldDelegate.color != color ||
      oldDelegate.selected != selected;
}

/// Global Studio Top Header Bar matching `.topbar` in `web/index.html` & `web/assets/styles.css`:
/// - Left: `[V] VidTrans` brand lockup
/// - Right: circular system status indicator + circular user avatar opening the Studio User Menu
/// - Bottom: `2.5px` orange gradient accent line
class StudioTopBar extends ConsumerWidget implements PreferredSizeWidget {
  const StudioTopBar({
    super.key,
    this.bottom,
    this.extraActions,
  });

  final PreferredSizeWidget? bottom;
  final List<Widget>? extraActions;

  @override
  Size get preferredSize =>
      Size.fromHeight(60 + (bottom?.preferredSize.height ?? 0));

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final jobsAsync = ref.watch(jobListProvider);
    final activeCount = jobsAsync.maybeWhen(
      data: (list) => list.jobs.where((j) => j.isActive).length,
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
                      onTap: () => context.go('/translate'),
                      borderRadius: BorderRadius.circular(10),
                      child: const Padding(
                        padding: EdgeInsets.symmetric(
                          horizontal: 2,
                          vertical: 4,
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            VidTransLogo(size: 32),
                            SizedBox(width: 9),
                            Text(
                              'VidTrans',
                              style: TextStyle(
                                fontSize: 17.5,
                                fontWeight: FontWeight.w800,
                                letterSpacing: -0.45,
                                color: AppColors.ink,
                              ),
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
                        context.go('/jobs');
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

  void _showStudioUserMenu(BuildContext context, WidgetRef ref) {
    final account = ref.read(sessionProvider).valueOrNull;
    showModalBottomSheet<void>(
      context: context,
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
                          _UserAvatarCircle(
                            name: account.name,
                            size: 46,
                          ),
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
                      subtitle: 'Tải lên logo, chọn góc hiển thị và chèn vào video',
                      onTap: () {
                        Navigator.of(sheetContext).pop();
                        context.push('/branding');
                      },
                    ),
                  _UserMenuTile(
                    icon: Icons.workspace_premium_outlined,
                    title: 'Gói dịch vụ & thanh toán',
                    subtitle: 'Xem hạn mức sử dụng, nâng cấp gói và lịch sử đơn',
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

class _UserMenuTile extends StatelessWidget {
  const _UserMenuTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.onTap,
    this.danger = false,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback onTap;
  final bool danger;

  @override
  Widget build(BuildContext context) {
    final color = danger ? AppColors.red : AppColors.ink;
    final iconBg = danger ? AppColors.redSoft : AppColors.brandSoft;
    final iconColor = danger ? AppColors.red : AppColors.brand;

    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(14),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
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
                    Text(
                      title,
                      style: TextStyle(
                        fontSize: 14,
                        fontWeight: FontWeight.w700,
                        color: color,
                      ),
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
                Icons.chevron_right_rounded,
                size: 20,
                color: danger ? AppColors.red : AppColors.muted,
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
  const _SystemStateCircle({
    required this.activeCount,
    required this.onTap,
  });

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
    return Tooltip(
      message: widget.activeCount > 0
          ? '${widget.activeCount} tiến trình đang xử lý'
          : 'Hệ thống sẵn sàng',
      child: InkWell(
        onTap: widget.onTap,
        customBorder: const CircleBorder(),
        child: Stack(
          clipBehavior: Clip.none,
          children: [
            Container(
              width: 34,
              height: 34,
              decoration: BoxDecoration(
                color: const Color(0xFFFFF7ED),
                shape: BoxShape.circle,
                border: Border.all(
                  color: const Color(0xFFFED7AA),
                  width: 1.2,
                ),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x14EA580C),
                    blurRadius: 6,
                    offset: Offset(0, 2),
                  ),
                ],
              ),
              alignment: Alignment.center,
              child: RotationTransition(
                turns: _controller,
                child: SizedBox(
                  width: 15,
                  height: 15,
                  child: CustomPaint(
                    painter: _SystemArcPainter(),
                  ),
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
  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    final paint = Paint()
      ..color = AppColors.brand
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.2
      ..strokeCap = StrokeCap.round;

    canvas.drawArc(
      rect.deflate(1.2),
      0.35,
      math.pi * 1.38,
      false,
      paint,
    );
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

/// Circular studio avatar badge matching `#userAvatar` in the top-right of the screenshot.
class _UserAvatarCircle extends StatelessWidget {
  const _UserAvatarCircle({
    required this.name,
    this.size = 36,
    this.onTap,
  });

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
            border: Border.all(
              color: const Color(0xFFCBD5E1),
              width: 1.2,
            ),
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
              border: Border.all(
                color: const Color(0xFFF59E0B),
                width: 1.3,
              ),
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
