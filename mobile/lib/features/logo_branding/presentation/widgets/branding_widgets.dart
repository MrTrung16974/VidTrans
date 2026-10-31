import 'dart:math';

import 'package:flutter/material.dart';

import '../../../../app/theme/app_theme.dart';
import '../../domain/branding_models.dart';
import 'auth_image.dart';

/// Where the logo lands at the default position, on a landscape and a
/// portrait frame. Same geometry as renderPreview() in web/js/branding.js.
class LogoPlacementPreview extends StatelessWidget {
  const LogoPlacementPreview({
    super.key,
    required this.config,
    required this.logo,
  });

  final BrandingConfig config;
  final BrandingImage? logo;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Expanded(flex: 16, child: _frame(context, 16 / 9, '16:9 NGANG')),
        const SizedBox(width: 12),
        Expanded(flex: 6, child: _frame(context, 9 / 16, '9:16 DỌC')),
      ],
    );
  }

  Widget _frame(BuildContext context, double aspect, String label) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        AspectRatio(
          aspectRatio: aspect,
          child: LayoutBuilder(
            builder: (context, constraints) {
              final width = constraints.maxWidth;
              final height = constraints.maxHeight;
              final image = logo;
              Widget? stamp;
              if (image != null && image.width > 0) {
                var logoWidth = width * config.logoSize / 100;
                var logoHeight = logoWidth * image.height / image.width;
                if (logoHeight > height * 0.3) {
                  logoWidth *= height * 0.3 / logoHeight;
                  logoHeight = height * 0.3;
                }
                final margin = min(width, height) * config.margin / 100;
                final top = config.defaultPosition.startsWith('top');
                final left = config.defaultPosition.endsWith('left');
                stamp = Positioned(
                  top: top ? margin : null,
                  bottom: top ? null : margin,
                  left: left ? margin : null,
                  right: left ? null : margin,
                  child: AuthImage(
                    image.url,
                    width: logoWidth,
                    height: logoHeight,
                    opacity: config.opacity,
                  ),
                );
              }
              return Container(
                decoration: BoxDecoration(
                  gradient: AppColors.darkStageGradient,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: const Color(0xFF475569)),
                  boxShadow: AppColors.cardShadow,
                ),
                clipBehavior: Clip.antiAlias,
                child: Stack(children: [?stamp]),
              );
            },
          ),
        ),
        const SizedBox(height: 6),
        Text(
          label,
          style: const TextStyle(
            fontSize: 10,
            fontWeight: FontWeight.w800,
            letterSpacing: 0.4,
            color: AppColors.muted,
          ),
        ),
      ],
    );
  }
}
