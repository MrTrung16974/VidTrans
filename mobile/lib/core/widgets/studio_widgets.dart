import 'package:flutter/material.dart';

import '../../app/theme/app_theme.dart';

/// Renders the exact VidTrans Creator Studio mark (`web/assets/vidtrans-mark.svg`):
/// - Warm orange gradient rounded square (`#FF985A` -> `#F16B3B` -> `#D94C22`)
/// - Crisp white "V" vector path
/// - Teal gradient play badge (`#239A94` -> `#12615F`) in the bottom-right corner
class VidTransLogo extends StatelessWidget {
  const VidTransLogo({super.key, this.size = 38});

  final double size;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: size,
      height: size,
      child: CustomPaint(
        painter: _VidTransLogoPainter(),
      ),
    );
  }
}

class _VidTransLogoPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final s = size.width / 64.0;

    // 1. Main rounded square background (x:4, y:4, w:56, h:56, rx:17)
    final bgRect = RRect.fromRectAndRadius(
      Rect.fromLTWH(4 * s, 4 * s, 56 * s, 56 * s),
      Radius.circular(17 * s),
    );
    final bgPaint = Paint()
      ..shader = const LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [
          Color(0xFFFF985A),
          Color(0xFFF16B3B),
          Color(0xFFD94C22),
        ],
        stops: [0.0, 0.52, 1.0],
      ).createShader(Rect.fromLTWH(6 * s, 6 * s, 52 * s, 52 * s));
    canvas.drawRRect(bgRect, bgPaint);

    // Subtle inner highlight border
    final strokePaint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5 * s
      ..color = Colors.white.withValues(alpha: 0.24);
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTWH(4.75 * s, 4.75 * s, 54.5 * s, 54.5 * s),
        Radius.circular(16.25 * s),
      ),
      strokePaint,
    );

    // 2. White "V" path: M17 19 H25.2 L31.9 36.2 L38.6 19 H46.7 L35.5 45 H28.2 L17 19 Z
    final vPath = Path()
      ..moveTo(17 * s, 19 * s)
      ..lineTo(25.2 * s, 19 * s)
      ..lineTo(31.9 * s, 36.2 * s)
      ..lineTo(38.6 * s, 19 * s)
      ..lineTo(46.7 * s, 19 * s)
      ..lineTo(35.5 * s, 45 * s)
      ..lineTo(28.2 * s, 45 * s)
      ..close();
    canvas.drawPath(vPath, Paint()..color = Colors.white);

    // 3. Teal circular play badge (cx:48, cy:47, r:10)
    final badgeCenter = Offset(48 * s, 47 * s);
    final badgeRadius = 10 * s;
    final badgeRect =
        Rect.fromCircle(center: badgeCenter, radius: badgeRadius);
    final badgePaint = Paint()
      ..shader = const LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [
          Color(0xFF239A94),
          Color(0xFF12615F),
        ],
      ).createShader(badgeRect);
    canvas.drawCircle(badgeCenter, badgeRadius, badgePaint);

    // White ring around badge (stroke-width: 2.5, opacity: 0.92)
    canvas.drawCircle(
      badgeCenter,
      badgeRadius,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.5 * s
        ..color = Colors.white.withValues(alpha: 0.92),
    );

    // White play triangle inside badge: M45.2 42.9 L52.1 47 L45.2 51.1 V42.9 Z
    final playPath = Path()
      ..moveTo(45.2 * s, 42.9 * s)
      ..lineTo(52.1 * s, 47 * s)
      ..lineTo(45.2 * s, 51.1 * s)
      ..close();
    canvas.drawPath(playPath, Paint()..color = Colors.white);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

/// Brand header lockup matching `.brand-mark` in `web/index.html`.
class VidTransBrandTitle extends StatelessWidget {
  const VidTransBrandTitle({
    super.key,
    this.logoSize = 36,
    this.subtitle = 'CREATOR STUDIO',
  });

  final double logoSize;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        VidTransLogo(size: logoSize),
        const SizedBox(width: 10),
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'VidTrans',
              style: TextStyle(
                fontSize: 18,
                fontWeight: FontWeight.w800,
                letterSpacing: -0.4,
                color: AppColors.ink,
                height: 1.1,
              ),
            ),
            const SizedBox(height: 1),
            Text(
              subtitle,
              style: const TextStyle(
                fontSize: 10,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.8,
                color: AppColors.muted,
                height: 1.1,
              ),
            ),
          ],
        ),
      ],
    );
  }
}

/// Matches `.eyebrow-chip` / `.jobs-eyebrow` on the web.
class StudioEyebrow extends StatelessWidget {
  const StudioEyebrow({
    super.key,
    required this.label,
    this.icon,
    this.color = AppColors.brandDark,
    this.backgroundColor = AppColors.brandSoft,
    this.borderColor = AppColors.brandBorder,
  });

  final String label;
  final IconData? icon;
  final Color color;
  final Color backgroundColor;
  final Color borderColor;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: backgroundColor,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: borderColor),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: 13, color: color),
            const SizedBox(width: 5),
          ],
          Text(
            label.toUpperCase(),
            style: TextStyle(
              fontSize: 10.5,
              fontWeight: FontWeight.w800,
              letterSpacing: 0.55,
              color: color,
            ),
          ),
        ],
      ),
    );
  }
}

/// System status pill matching `.system-state` in the web top bar.
class StudioSystemBadge extends StatelessWidget {
  const StudioSystemBadge({
    super.key,
    required this.label,
    this.busy = false,
  });

  final String label;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final bg = busy ? const Color(0xFFFFF7ED) : AppColors.greenSoft;
    final fg = busy ? const Color(0xFF9A3412) : AppColors.greenText;
    final border = busy ? AppColors.brandBorder : AppColors.greenBorder;
    final dot = busy ? AppColors.brand : AppColors.green;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: border),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(
              color: dot,
              shape: BoxShape.circle,
              boxShadow: [
                BoxShadow(
                  color: dot.withValues(alpha: 0.25),
                  spreadRadius: 2,
                ),
              ],
            ),
          ),
          const SizedBox(width: 6),
          Text(
            label,
            style: TextStyle(
              fontSize: 11.5,
              fontWeight: FontWeight.w700,
              color: fg,
            ),
          ),
        ],
      ),
    );
  }
}

enum StudioStatusTone {
  brand,
  success,
  warning,
  danger,
  info,
  neutral,
}

/// Semantic status pill with indicator dot matching `.status-pill` in `web/assets/styles.css`.
class StudioStatusPill extends StatelessWidget {
  const StudioStatusPill({
    super.key,
    this.status = 'neutral',
    required this.label,
    this.tone,
    this.showDot = true,
    this.compact = false,
  });

  final String status;
  final String label;
  final StudioStatusTone? tone;
  final bool showDot;
  final bool compact;

  ({Color bg, Color fg, Color border, Color dot}) _palette() {
    if (tone != null) {
      return switch (tone!) {
        StudioStatusTone.brand => (
            bg: AppColors.brandSoft,
            fg: AppColors.brandDark,
            border: AppColors.brandBorder,
            dot: AppColors.brand,
          ),
        StudioStatusTone.success => (
            bg: AppColors.greenSoft,
            fg: AppColors.greenText,
            border: AppColors.greenBorder,
            dot: AppColors.green,
          ),
        StudioStatusTone.info => (
            bg: AppColors.blueSoft,
            fg: AppColors.blueText,
            border: AppColors.blueBorder,
            dot: AppColors.blue,
          ),
        StudioStatusTone.warning => (
            bg: AppColors.amberSoft,
            fg: AppColors.amberText,
            border: AppColors.amberBorder,
            dot: AppColors.amber,
          ),
        StudioStatusTone.danger => (
            bg: AppColors.redSoft,
            fg: AppColors.redText,
            border: AppColors.redBorder,
            dot: AppColors.red,
          ),
        StudioStatusTone.neutral => (
            bg: AppColors.surfaceMuted,
            fg: AppColors.inkSoft,
            border: AppColors.line,
            dot: AppColors.muted,
          ),
      };
    }
    return switch (status) {
      'completed' || 'ready' || 'active' || 'paid' => (
          bg: AppColors.greenSoft,
          fg: AppColors.greenText,
          border: AppColors.greenBorder,
          dot: AppColors.green,
        ),
      'processing' ||
      'running' ||
      'generating' ||
      'rendering' ||
      'analyzing' =>
        (
          bg: AppColors.blueSoft,
          fg: AppColors.blueText,
          border: AppColors.blueBorder,
          dot: AppColors.blue,
        ),
      'queued' || 'pending' || 'cancelling' || 'draft' || 'stale' => (
          bg: AppColors.amberSoft,
          fg: AppColors.amberText,
          border: AppColors.amberBorder,
          dot: AppColors.amber,
        ),
      'scheduled' => (
          bg: AppColors.tealSoft,
          fg: AppColors.tealText,
          border: AppColors.tealBorder,
          dot: AppColors.teal,
        ),
      'failed' || 'cancelled' || 'error' || 'expired' => (
          bg: AppColors.redSoft,
          fg: AppColors.redText,
          border: AppColors.redBorder,
          dot: AppColors.red,
        ),
      _ => (
          bg: AppColors.surfaceMuted,
          fg: AppColors.inkSoft,
          border: AppColors.line,
          dot: AppColors.muted,
        ),
    };
  }

  @override
  Widget build(BuildContext context) {
    final p = _palette();
    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: compact ? 8 : 10,
        vertical: compact ? 3 : 4,
      ),
      decoration: BoxDecoration(
        color: p.bg,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: p.border),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (showDot) ...[
            Container(
              width: 6,
              height: 6,
              decoration: BoxDecoration(
                color: p.dot,
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 5),
          ],
          Flexible(
            child: Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontSize: compact ? 11 : 11.5,
                fontWeight: FontWeight.w700,
                color: p.fg,
                height: 1.15,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Card container matching `.card` / `.step-card` in `web/assets/styles.css`.
class StudioCard extends StatelessWidget {
  const StudioCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(16),
    this.margin,
    this.topAccentColor,
    this.leftAccentColor,
    this.color,
    this.backgroundColor = AppColors.surface,
    this.borderColor = AppColors.line,
    this.borderRadius = 18,
    this.onTap,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final EdgeInsetsGeometry? margin;
  final Color? topAccentColor;
  final Color? leftAccentColor;
  final Color? color;
  final Color backgroundColor;
  final Color borderColor;
  final double borderRadius;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final resolvedBg = color ?? backgroundColor;
    Widget content = Padding(
      padding: padding,
      child: child,
    );

    if (topAccentColor != null && leftAccentColor == null) {
      content = Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            height: 3.5,
            decoration: BoxDecoration(
              color: topAccentColor,
              borderRadius: BorderRadius.vertical(
                top: Radius.circular(borderRadius),
              ),
            ),
          ),
          Padding(
            padding: padding,
            child: child,
          ),
        ],
      );
    } else if (leftAccentColor != null) {
      content = Stack(
        children: [
          Padding(
            padding: padding,
            child: child,
          ),
          Positioned(
            left: 0,
            top: 0,
            bottom: 0,
            width: 4.5,
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [
                    leftAccentColor!,
                    leftAccentColor!.withValues(alpha: 0.72),
                  ],
                ),
              ),
            ),
          ),
        ],
      );
    }

    return Container(
      margin: margin,
      decoration: BoxDecoration(
        color: resolvedBg,
        borderRadius: BorderRadius.circular(borderRadius),
        border: Border.all(color: borderColor),
        boxShadow: AppColors.cardShadow,
      ),
      clipBehavior: Clip.antiAlias,
      child: onTap != null
          ? Material(
              color: Colors.transparent,
              child: InkWell(
                onTap: onTap,
                borderRadius: BorderRadius.circular(borderRadius),
                child: content,
              ),
            )
          : content,
    );
  }
}

/// Numbered or icon step header matching `.step-heading` and `.step-badge` in `web/index.html`.
class StudioStepHeader extends StatelessWidget {
  const StudioStepHeader({
    super.key,
    this.step,
    this.stepNumber,
    this.icon,
    required this.title,
    this.subtitle,
    this.trailing,
    this.accentColor = AppColors.brand,
    this.darkBadge = true,
  });

  final int? step;
  final int? stepNumber;
  final IconData? icon;
  final String title;
  final String? subtitle;
  final Widget? trailing;
  final Color accentColor;
  final bool darkBadge;

  @override
  Widget build(BuildContext context) {
    final number = step ?? stepNumber;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (number != null)
          Container(
            width: 34,
            height: 34,
            decoration: BoxDecoration(
              gradient: darkBadge
                  ? const LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: [Color(0xFF1E293B), Color(0xFF0F172A)],
                    )
                  : (accentColor == AppColors.brand
                        ? AppColors.brandGradient
                        : null),
              color: darkBadge || accentColor == AppColors.brand
                  ? null
                  : accentColor,
              borderRadius: BorderRadius.circular(10),
              boxShadow: [
                BoxShadow(
                  color: (darkBadge ? AppColors.ink : accentColor)
                      .withValues(alpha: 0.18),
                  blurRadius: 8,
                  offset: const Offset(0, 3),
                ),
              ],
            ),
            alignment: Alignment.center,
            child: Text(
              '$number',
              style: const TextStyle(
                color: Colors.white,
                fontWeight: FontWeight.w800,
                fontSize: 14,
              ),
            ),
          )
        else if (icon != null)
          Container(
            width: 34,
            height: 34,
            decoration: BoxDecoration(
              color: accentColor.withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: accentColor.withValues(alpha: 0.22)),
            ),
            alignment: Alignment.center,
            child: Icon(icon, size: 18, color: accentColor),
          ),
        if (number != null || icon != null) const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: const TextStyle(
                  fontSize: 16.5,
                  fontWeight: FontWeight.w800,
                  color: AppColors.ink,
                  letterSpacing: -0.3,
                ),
              ),
              if (subtitle != null && subtitle!.isNotEmpty) ...[
                const SizedBox(height: 3),
                Text(
                  subtitle!,
                  style: const TextStyle(
                    fontSize: 12.8,
                    color: AppColors.muted,
                    height: 1.4,
                  ),
                ),
              ],
            ],
          ),
        ),
        if (trailing != null) ...[
          const SizedBox(width: 8),
          trailing!,
        ],
      ],
    );
  }
}

/// Primary gradient button matching `.button.primary` in `web/assets/styles.css`.
class StudioGradientButton extends StatelessWidget {
  const StudioGradientButton({
    super.key,
    required this.onPressed,
    required this.label,
    this.icon,
    this.isLoading = false,
    this.expanded = true,
    this.padding = const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
  });

  final VoidCallback? onPressed;
  final String label;
  final IconData? icon;
  final bool isLoading;
  final bool expanded;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) {
    final enabled = onPressed != null && !isLoading;
    final child = Row(
      mainAxisSize: expanded ? MainAxisSize.max : MainAxisSize.min,
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        if (isLoading) ...[
          const SizedBox(
            width: 18,
            height: 18,
            child: CircularProgressIndicator(
              strokeWidth: 2.2,
              color: Colors.white,
            ),
          ),
          const SizedBox(width: 10),
        ] else if (icon != null) ...[
          Icon(icon, size: 19, color: Colors.white),
          const SizedBox(width: 8),
        ],
        Flexible(
          child: Text(
            label,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 14.5,
              fontWeight: FontWeight.w700,
              letterSpacing: -0.1,
            ),
          ),
        ),
      ],
    );

    return AnimatedOpacity(
      duration: const Duration(milliseconds: 150),
      opacity: enabled ? 1.0 : 0.55,
      child: DecoratedBox(
        decoration: BoxDecoration(
          gradient: AppColors.brandGradient,
          borderRadius: BorderRadius.circular(12),
          boxShadow: enabled ? AppColors.brandGlowShadow : null,
        ),
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            onTap: enabled ? onPressed : null,
            borderRadius: BorderRadius.circular(12),
            child: Padding(
              padding: padding,
              child: child,
            ),
          ),
        ),
      ),
    );
  }
}

/// Dashed empty state box matching `.tiktok-empty` and `.drop-zone` in `web/assets/styles.css`.
class StudioEmptyState extends StatelessWidget {
  const StudioEmptyState({
    super.key,
    required this.icon,
    required this.title,
    this.subtitle,
    this.action,
  });

  final IconData icon;
  final String title;
  final String? subtitle;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Container(
          constraints: const BoxConstraints(maxWidth: 420),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
          decoration: BoxDecoration(
            color: AppColors.surface,
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: AppColors.lineStrong, width: 1.2),
            boxShadow: AppColors.cardShadow,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 56,
                height: 56,
                decoration: BoxDecoration(
                  color: AppColors.brandSoft,
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(color: AppColors.brandBorder),
                ),
                alignment: Alignment.center,
                child: Icon(icon, size: 28, color: AppColors.brand),
              ),
              const SizedBox(height: 16),
              Text(
                title,
                textAlign: TextAlign.center,
                style: const TextStyle(
                  fontSize: 16,
                  fontWeight: FontWeight.w800,
                  color: AppColors.ink,
                ),
              ),
              if (subtitle != null && subtitle!.isNotEmpty) ...[
                const SizedBox(height: 6),
                Text(
                  subtitle!,
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                    fontSize: 13,
                    color: AppColors.muted,
                    height: 1.45,
                  ),
                ),
              ],
              if (action != null) ...[
                const SizedBox(height: 18),
                action!,
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// Metric card matching `.metric-card` in `web/assets/styles.css`.
class StudioMetricCard extends StatelessWidget {
  const StudioMetricCard({
    super.key,
    required this.label,
    required this.value,
    this.subtitle,
    this.accentColor = AppColors.ink,
    this.backgroundColor = AppColors.surface,
    this.borderColor = AppColors.line,
    this.icon,
    this.iconBackgroundColor,
  });

  final String label;
  final String value;
  final String? subtitle;
  final Color accentColor;
  final Color backgroundColor;
  final Color borderColor;
  final IconData? icon;
  final Color? iconBackgroundColor;

  @override
  Widget build(BuildContext context) {
    final resolvedIconBg = iconBackgroundColor ??
        (accentColor == AppColors.ink
            ? AppColors.surfaceMuted
            : accentColor.withValues(alpha: 0.12));

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 12),
      decoration: BoxDecoration(
        color: backgroundColor,
        borderRadius: BorderRadius.circular(15),
        border: Border.all(color: borderColor),
        boxShadow: AppColors.cardShadow,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  label.toUpperCase(),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 10.5,
                    fontWeight: FontWeight.w800,
                    letterSpacing: 0.45,
                    color: AppColors.muted,
                  ),
                ),
              ),
              if (icon != null) ...[
                const SizedBox(width: 6),
                Container(
                  width: 26,
                  height: 26,
                  decoration: BoxDecoration(
                    color: resolvedIconBg,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  alignment: Alignment.center,
                  child: Icon(icon, size: 14, color: accentColor),
                ),
              ],
            ],
          ),
          const SizedBox(height: 6),
          Text(
            value,
            style: TextStyle(
              fontSize: 22,
              fontWeight: FontWeight.w900,
              color: accentColor,
              letterSpacing: -0.6,
              height: 1.08,
            ),
          ),
          if (subtitle != null && subtitle!.isNotEmpty) ...[
            const SizedBox(height: 3),
            Text(
              subtitle!,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w500,
                color: AppColors.muted,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// Web-style page header matching `.page-heading` / `.jobs-header` in `web/index.html`
/// and `web/assets/styles.css`. Adapts automatically to all phone widths.
class StudioPageHeader extends StatelessWidget {
  const StudioPageHeader({
    super.key,
    this.eyebrow,
    this.eyebrowIcon,
    this.eyebrowPulse = false,
    this.eyebrowNote,
    required this.title,
    this.subtitle,
    this.actions,
    this.bottom,
  });

  final String? eyebrow;
  final IconData? eyebrowIcon;
  final bool eyebrowPulse;
  final String? eyebrowNote;
  final String title;
  final String? subtitle;
  final List<Widget>? actions;
  final Widget? bottom;

  @override
  Widget build(BuildContext context) {
    final screenWidth = MediaQuery.sizeOf(context).width;
    final isCompact = screenWidth < 360;

    return Container(
      padding: EdgeInsets.all(isCompact ? 14 : 16),
      decoration: BoxDecoration(
        gradient: AppColors.studioHeroGradient,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.brandBorder.withValues(alpha: 0.85)),
        boxShadow: AppColors.cardShadow,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (eyebrow != null || eyebrowNote != null) ...[
            Wrap(
              spacing: 8,
              runSpacing: 6,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                if (eyebrow != null)
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 10,
                      vertical: 4,
                    ),
                    decoration: BoxDecoration(
                      color: AppColors.brandSoft,
                      borderRadius: BorderRadius.circular(999),
                      border: Border.all(color: AppColors.brandBorder),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (eyebrowPulse) ...[
                          Container(
                            width: 7,
                            height: 7,
                            decoration: BoxDecoration(
                              color: AppColors.brand,
                              shape: BoxShape.circle,
                              boxShadow: [
                                BoxShadow(
                                  color: AppColors.brand.withValues(alpha: 0.35),
                                  spreadRadius: 2,
                                ),
                              ],
                            ),
                          ),
                          const SizedBox(width: 6),
                        ] else if (eyebrowIcon != null) ...[
                          Icon(
                            eyebrowIcon,
                            size: 13,
                            color: AppColors.brandDark,
                          ),
                          const SizedBox(width: 5),
                        ],
                        Flexible(
                          child: Text(
                            eyebrow!.toUpperCase(),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 10.5,
                              fontWeight: FontWeight.w800,
                              letterSpacing: 0.5,
                              color: AppColors.brandDark,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                if (eyebrowNote != null)
                  Text(
                    eyebrowNote!,
                    style: const TextStyle(
                      fontSize: 11.5,
                      fontWeight: FontWeight.w600,
                      color: AppColors.muted,
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 10),
          ],
          Text(
            title,
            style: TextStyle(
              fontSize: isCompact ? 18.5 : 20.5,
              fontWeight: FontWeight.w900,
              color: AppColors.ink,
              letterSpacing: -0.5,
              height: 1.2,
            ),
          ),
          if (subtitle != null && subtitle!.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text(
              subtitle!,
              style: TextStyle(
                fontSize: isCompact ? 12.5 : 13,
                color: AppColors.muted,
                height: 1.45,
              ),
            ),
          ],
          if (actions != null && actions!.isNotEmpty) ...[
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: actions!,
            ),
          ],
          if (bottom != null) ...[
            const SizedBox(height: 12),
            bottom!,
          ],
        ],
      ),
    );
  }
}

/// Top navigation bar for sub-screens pushed onto the stack, styled to match
/// `.topbar` in `web/index.html` & `web/assets/styles.css` with a 2.5px orange
/// gradient bottom accent line.
class StudioSubBar extends StatelessWidget implements PreferredSizeWidget {
  const StudioSubBar({
    super.key,
    required this.title,
    this.subtitle,
    this.actions,
    this.bottom,
    this.onBack,
  });

  final String title;
  final String? subtitle;
  final List<Widget>? actions;
  final PreferredSizeWidget? bottom;
  final VoidCallback? onBack;

  @override
  Size get preferredSize =>
      Size.fromHeight(58 + (bottom?.preferredSize.height ?? 0));

  @override
  Widget build(BuildContext context) {
    final canPop = Navigator.of(context).canPop();
    return Material(
      color: Colors.white,
      child: SafeArea(
        bottom: false,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            SizedBox(
              height: 55.5,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: Row(
                  children: [
                    if (canPop || onBack != null) ...[
                      InkWell(
                        onTap: onBack ?? () => Navigator.of(context).maybePop(),
                        borderRadius: BorderRadius.circular(11),
                        child: Container(
                          width: 36,
                          height: 36,
                          decoration: BoxDecoration(
                            color: AppColors.surfaceSoft,
                            borderRadius: BorderRadius.circular(11),
                            border: Border.all(color: AppColors.line),
                          ),
                          alignment: Alignment.center,
                          child: const Icon(
                            Icons.arrow_back_ios_new_rounded,
                            size: 16,
                            color: AppColors.ink,
                          ),
                        ),
                      ),
                      const SizedBox(width: 10),
                    ] else ...[
                      const VidTransLogo(size: 30),
                      const SizedBox(width: 10),
                    ],
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Text(
                            title,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 16.5,
                              fontWeight: FontWeight.w800,
                              letterSpacing: -0.35,
                              color: AppColors.ink,
                              height: 1.15,
                            ),
                          ),
                          if (subtitle != null && subtitle!.isNotEmpty) ...[
                            const SizedBox(height: 1),
                            Text(
                              subtitle!,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                fontSize: 11.5,
                                fontWeight: FontWeight.w600,
                                color: AppColors.muted,
                                height: 1.1,
                              ),
                            ),
                          ],
                        ],
                      ),
                    ),
                    if (actions != null && actions!.isNotEmpty) ...[
                      const SizedBox(width: 6),
                      Row(
                        mainAxisSize: MainAxisSize.min,
                        children: actions!,
                      ),
                    ],
                  ],
                ),
              ),
            ),
            ?bottom,
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
}

/// Dashed border container matching `.drop-zone` in `web/assets/styles.css`.
class StudioDashedBox extends StatelessWidget {
  const StudioDashedBox({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(16),
    this.borderRadius = 16,
    this.borderColor = const Color(0xFFCBD5E1),
    this.backgroundColor = const Color(0xFFFCFDFE),
    this.onTap,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final double borderRadius;
  final Color borderColor;
  final Color backgroundColor;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final inner = CustomPaint(
      foregroundPainter: _DashedRRectPainter(
        color: borderColor,
        radius: borderRadius,
      ),
      child: Container(
        padding: padding,
        decoration: BoxDecoration(
          color: backgroundColor,
          borderRadius: BorderRadius.circular(borderRadius),
        ),
        child: child,
      ),
    );

    if (onTap == null) return inner;
    return Material(
      color: Colors.transparent,
      borderRadius: BorderRadius.circular(borderRadius),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(borderRadius),
        child: inner,
      ),
    );
  }
}

class _DashedRRectPainter extends CustomPainter {
  const _DashedRRectPainter({
    required this.color,
    required this.radius,
  });

  final Color color;
  final double radius;

  @override
  void paint(Canvas canvas, Size size) {
    final rrect = RRect.fromRectAndRadius(
      Offset.zero & size,
      Radius.circular(radius),
    );
    final path = Path()..addRRect(rrect);
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.4;

    const dashWidth = 5.5;
    const dashSpace = 4.0;
    for (final metric in path.computeMetrics()) {
      var distance = 0.0;
      while (distance < metric.length) {
        final next = (distance + dashWidth).clamp(0.0, metric.length);
        canvas.drawPath(metric.extractPath(distance, next), paint);
        distance += dashWidth + dashSpace;
      }
    }
  }

  @override
  bool shouldRepaint(covariant _DashedRRectPainter oldDelegate) =>
      oldDelegate.color != color || oldDelegate.radius != radius;
}
