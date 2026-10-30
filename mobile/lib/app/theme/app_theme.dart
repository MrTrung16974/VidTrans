import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Design tokens and Material 3 theme matching the VidTrans Studio web app
/// (`web/assets/styles.css`).
class AppColors {
  const AppColors._();

  // Surfaces & backgrounds (--bg, --surface, --surface-soft)
  static const bg = Color(0xFFF8FAFC);
  static const surface = Color(0xFFFFFFFF);
  static const surfaceSoft = Color(0xFFF8FAFC);
  static const surfaceSubtle = Color(0xFFF8FAFC);
  static const surfaceMuted = Color(0xFFF1F5F9);

  // Typography (--ink, --muted, --muted-light)
  static const ink = Color(0xFF0F172A);
  static const inkSecondary = Color(0xFF334155);
  static const inkSoft = Color(0xFF334155);
  static const muted = Color(0xFF64748B);
  static const mutedLight = Color(0xFF94A3B8);

  // Lines & borders (--line, --line-subtle)
  static const line = Color(0xFFE2E8F0);
  static const lineStrong = Color(0xFFCBD5E1);
  static const lineSubtle = Color(0xFFF1F5F9);

  // Brand Orange (--brand, --brand-dark, --brand-light)
  static const brand = Color(0xFFEA580C);
  static const brandBright = Color(0xFFF97316);
  static const brandDark = Color(0xFFC2410C);
  static const brandLight = Color(0xFFFFEDD5);
  static const brandSoft = Color(0xFFFFEDD5);
  static const brandTint = Color(0xFFFFF7ED);
  static const brandBorder = Color(0xFFFED7AA);

  // Studio Teal (--teal)
  static const teal = Color(0xFF0F766E);
  static const tealBright = Color(0xFF14B8A6);
  static const tealLight = Color(0xFFCCFBF1);
  static const tealSoft = Color(0xFFF0FDFA);
  static const tealTint = Color(0xFFF0FDFA);
  static const tealBorder = Color(0xFF99F6E4);
  static const tealText = Color(0xFF115E59);

  // Semantic accents (--blue, --green, --red, --amber)
  static const blue = Color(0xFF2563EB);
  static const sky = Color(0xFF0284C7);
  static const blueTint = Color(0xFFEFF6FF);
  static const blueSoft = Color(0xFFEFF6FF);
  static const blueBorder = Color(0xFFBFDBFE);
  static const blueInk = Color(0xFF1E40AF);
  static const blueText = Color(0xFF1E40AF);

  static const green = Color(0xFF10B981);
  static const greenDark = Color(0xFF059669);
  static const greenTint = Color(0xFFECFDF5);
  static const greenSoft = Color(0xFFECFDF5);
  static const greenBorder = Color(0xFFA7F3D0);
  static const greenInk = Color(0xFF065F46);
  static const greenText = Color(0xFF065F46);

  static const amber = Color(0xFFF59E0B);
  static const amberDark = Color(0xFFD97706);
  static const amberTint = Color(0xFFFFFBEB);
  static const amberSoft = Color(0xFFFFFBEB);
  static const amberBorder = Color(0xFFFDE68A);
  static const amberInk = Color(0xFF92400E);
  static const amberText = Color(0xFF92400E);

  static const red = Color(0xFFEF4444);
  static const redDark = Color(0xFFDC2626);
  static const redTint = Color(0xFFFEF2F2);
  static const redSoft = Color(0xFFFEF2F2);
  static const redBorder = Color(0xFFFECACA);
  static const redInk = Color(0xFF991B1B);
  static const redText = Color(0xFF991B1B);

  // Semantic aliases used across feature screens
  static const primary = brand;
  static const primarySoft = brandSoft;
  static const primaryBorder = brandBorder;
  static const secondary = teal;
  static const secondarySoft = tealSoft;
  static const textPrimary = ink;
  static const textSecondary = inkSecondary;
  static const textMuted = muted;
  static const border = line;
  static const danger = redDark;
  static const warning = amberDark;
  static const warningBg = amberSoft;
  static const warningBorder = amberBorder;
  static const darkBg = Color(0xFF090D16);
  static const darkSurface = Color(0xFF111827);
  static const darkText = Color(0xFFF8FAFC);

  static const studioHeroGradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFFFFFBF7), Color(0xFFFFFFFF), Color(0xFFF0FDFA)],
  );

  static const cardShadow = <BoxShadow>[
    BoxShadow(
      color: Color(0x0A0F172A),
      blurRadius: 10,
      offset: Offset(0, 2),
    ),
  ];

  static const elevatedShadow = <BoxShadow>[
    BoxShadow(
      color: Color(0x140F172A),
      blurRadius: 24,
      offset: Offset(0, 8),
    ),
  ];

  static const brandGlowShadow = <BoxShadow>[
    BoxShadow(
      color: Color(0x38EA580C),
      blurRadius: 14,
      offset: Offset(0, 4),
    ),
  ];

  // Gradients from web CSS
  static const brandGradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFFF97316), Color(0xFFEA580C), Color(0xFFC2410C)],
    stops: [0.0, 0.55, 1.0],
  );

  static const tealGradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF14B8A6), Color(0xFF0F766E)],
  );

  static const darkStageGradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF1E293B), Color(0xFF0F172A)],
  );

  static const subtitlePreviewGradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF090D16), Color(0xFF1E293B), Color(0xFF7C2D12)],
    stops: [0.0, 0.55, 1.0],
  );
}

class AppTheme {
  static ThemeData light() => _build(Brightness.light);

  /// The web studio is designed as a crisp, high-contrast light workspace
  /// (`color-scheme: light`); dark() uses a cohesive slate-dark adaptation
  /// with the same brand orange & teal accents.
  static ThemeData dark() => _build(Brightness.dark);

  static ThemeData _build(Brightness brightness) {
    final isDark = brightness == Brightness.dark;
    final bg = isDark ? const Color(0xFF0B1120) : AppColors.bg;
    final surface = isDark ? const Color(0xFF111827) : AppColors.surface;
    final surfaceSoft = isDark ? const Color(0xFF1E293B) : AppColors.surfaceSoft;
    final ink = isDark ? const Color(0xFFF8FAFC) : AppColors.ink;
    final muted = isDark ? const Color(0xFF94A3B8) : AppColors.muted;
    final line = isDark ? const Color(0xFF1E293B) : AppColors.line;
    final lineStrong = isDark ? const Color(0xFF334155) : AppColors.lineStrong;

    final baseScheme = ColorScheme.fromSeed(
      seedColor: AppColors.brand,
      brightness: brightness,
    );

    final scheme = baseScheme.copyWith(
      primary: AppColors.brand,
      onPrimary: Colors.white,
      primaryContainer: isDark ? const Color(0xFF431407) : AppColors.brandLight,
      onPrimaryContainer: isDark ? const Color(0xFFFFEDD5) : AppColors.brandDark,
      secondary: AppColors.teal,
      onSecondary: Colors.white,
      secondaryContainer: isDark ? const Color(0xFF134E4A) : AppColors.tealLight,
      onSecondaryContainer: isDark ? const Color(0xFFCCFBF1) : AppColors.teal,
      tertiary: AppColors.sky,
      onTertiary: Colors.white,
      tertiaryContainer: isDark ? const Color(0xFF0C4A6E) : AppColors.blueTint,
      onTertiaryContainer: isDark ? const Color(0xFFE0F2FE) : AppColors.blueInk,
      error: AppColors.redDark,
      onError: Colors.white,
      errorContainer: isDark ? const Color(0xFF450A0A) : AppColors.redTint,
      onErrorContainer: isDark ? const Color(0xFFFECACA) : AppColors.redInk,
      surface: surface,
      onSurface: ink,
      onSurfaceVariant: muted,
      surfaceContainerHighest: isDark ? const Color(0xFF1E293B) : AppColors.surfaceMuted,
      surfaceContainerHigh: surfaceSoft,
      surfaceContainer: surfaceSoft,
      surfaceContainerLow: surface,
      outline: lineStrong,
      outlineVariant: line,
    );

    final baseText = isDark ? ThemeData.dark().textTheme : ThemeData.light().textTheme;
    final textTheme = baseText
        .apply(
          bodyColor: ink,
          displayColor: ink,
        )
        .copyWith(
          displaySmall: baseText.displaySmall?.copyWith(
            fontWeight: FontWeight.w800,
            letterSpacing: -0.8,
            color: ink,
          ),
          headlineMedium: baseText.headlineMedium?.copyWith(
            fontWeight: FontWeight.w800,
            letterSpacing: -0.6,
            color: ink,
          ),
          headlineSmall: baseText.headlineSmall?.copyWith(
            fontWeight: FontWeight.w800,
            letterSpacing: -0.5,
            color: ink,
          ),
          titleLarge: baseText.titleLarge?.copyWith(
            fontWeight: FontWeight.w800,
            fontSize: 20,
            letterSpacing: -0.4,
            color: ink,
          ),
          titleMedium: baseText.titleMedium?.copyWith(
            fontWeight: FontWeight.w700,
            fontSize: 16,
            letterSpacing: -0.2,
            color: ink,
          ),
          titleSmall: baseText.titleSmall?.copyWith(
            fontWeight: FontWeight.w700,
            fontSize: 14,
            letterSpacing: -0.1,
            color: ink,
          ),
          bodyLarge: baseText.bodyLarge?.copyWith(
            fontSize: 15,
            height: 1.45,
            color: ink,
          ),
          bodyMedium: baseText.bodyMedium?.copyWith(
            fontSize: 14,
            height: 1.45,
            color: ink,
          ),
          bodySmall: baseText.bodySmall?.copyWith(
            fontSize: 12.5,
            height: 1.4,
            fontWeight: FontWeight.w500,
            color: muted,
          ),
          labelLarge: baseText.labelLarge?.copyWith(
            fontWeight: FontWeight.w700,
            fontSize: 13.5,
            letterSpacing: -0.1,
          ),
          labelMedium: baseText.labelMedium?.copyWith(
            fontWeight: FontWeight.w700,
            fontSize: 12,
          ),
          labelSmall: baseText.labelSmall?.copyWith(
            fontWeight: FontWeight.w700,
            fontSize: 11,
            letterSpacing: 0.1,
          ),
        );

    final inputBorder = OutlineInputBorder(
      borderRadius: BorderRadius.circular(12),
      borderSide: BorderSide(color: lineStrong, width: 1),
    );
    final enabledBorder = OutlineInputBorder(
      borderRadius: BorderRadius.circular(12),
      borderSide: BorderSide(color: line, width: 1.2),
    );
    final focusedBorder = OutlineInputBorder(
      borderRadius: BorderRadius.circular(12),
      borderSide: const BorderSide(color: AppColors.brand, width: 1.8),
    );
    final errorBorder = OutlineInputBorder(
      borderRadius: BorderRadius.circular(12),
      borderSide: const BorderSide(color: AppColors.red, width: 1.4),
    );

    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme,
      scaffoldBackgroundColor: bg,
      textTheme: textTheme,
      dividerColor: line,
      dividerTheme: DividerThemeData(color: line, thickness: 1, space: 1),
      appBarTheme: AppBarTheme(
        centerTitle: false,
        elevation: 0,
        scrolledUnderElevation: 0.5,
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        foregroundColor: ink,
        systemOverlayStyle: isDark ? SystemUiOverlayStyle.light : SystemUiOverlayStyle.dark,
        titleTextStyle: textTheme.titleLarge?.copyWith(
          fontSize: 18,
          fontWeight: FontWeight.w800,
          letterSpacing: -0.35,
          color: ink,
        ),
        shape: Border(bottom: BorderSide(color: line, width: 1)),
      ),
      cardTheme: CardThemeData(
        color: surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        margin: const EdgeInsets.symmetric(vertical: 6),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(18),
          side: BorderSide(color: line, width: 1),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: isDark ? const Color(0xFF0F172A) : AppColors.surface,
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 13),
        border: inputBorder,
        enabledBorder: enabledBorder,
        focusedBorder: focusedBorder,
        errorBorder: errorBorder,
        focusedErrorBorder: errorBorder,
        labelStyle: TextStyle(color: muted, fontWeight: FontWeight.w600, fontSize: 13.5),
        floatingLabelStyle: const TextStyle(
          color: AppColors.brand,
          fontWeight: FontWeight.w700,
          fontSize: 13.5,
        ),
        hintStyle: TextStyle(color: isDark ? const Color(0xFF64748B) : AppColors.mutedLight, fontSize: 13.5),
        helperStyle: TextStyle(color: muted, fontSize: 12),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size.fromHeight(48),
          backgroundColor: AppColors.brand,
          foregroundColor: Colors.white,
          disabledBackgroundColor: isDark ? const Color(0xFF1E293B) : AppColors.surfaceMuted,
          disabledForegroundColor: AppColors.mutedLight,
          elevation: 0,
          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
          textStyle: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14.5, letterSpacing: -0.1),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(13)),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size.fromHeight(46),
          foregroundColor: ink,
          backgroundColor: surface,
          side: BorderSide(color: lineStrong, width: 1),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 11),
          textStyle: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14, letterSpacing: -0.1),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: AppColors.brandDark,
          textStyle: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        ),
      ),
      floatingActionButtonTheme: FloatingActionButtonThemeData(
        backgroundColor: AppColors.brand,
        foregroundColor: Colors.white,
        elevation: 3,
        extendedTextStyle: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14, letterSpacing: -0.1),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: surfaceSoft,
        selectedColor: isDark ? const Color(0xFF431407) : AppColors.brandTint,
        disabledColor: surfaceSoft,
        side: BorderSide(color: line, width: 1),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(999)),
        labelStyle: TextStyle(color: ink, fontWeight: FontWeight.w600, fontSize: 12.5),
        secondaryLabelStyle: const TextStyle(
          color: AppColors.brandDark,
          fontWeight: FontWeight.w700,
          fontSize: 12.5,
        ),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      ),
      segmentedButtonTheme: SegmentedButtonThemeData(
        style: ButtonStyle(
          backgroundColor: WidgetStateProperty.resolveWith((states) {
            if (states.contains(WidgetState.selected)) {
              return isDark ? const Color(0xFF431407) : AppColors.brandTint;
            }
            return surface;
          }),
          foregroundColor: WidgetStateProperty.resolveWith((states) {
            if (states.contains(WidgetState.selected)) {
              return isDark ? AppColors.brandLight : AppColors.brandDark;
            }
            return muted;
          }),
          side: WidgetStateProperty.resolveWith((states) {
            if (states.contains(WidgetState.selected)) {
              return const BorderSide(color: AppColors.brand, width: 1.4);
            }
            return BorderSide(color: line, width: 1);
          }),
          textStyle: WidgetStateProperty.all(
            const TextStyle(fontWeight: FontWeight.w700, fontSize: 13),
          ),
          shape: WidgetStateProperty.all(
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          ),
        ),
      ),
      tabBarTheme: TabBarThemeData(
        labelColor: AppColors.brandDark,
        unselectedLabelColor: muted,
        indicatorColor: AppColors.brand,
        indicatorSize: TabBarIndicatorSize.tab,
        dividerColor: line,
        labelStyle: const TextStyle(fontWeight: FontWeight.w800, fontSize: 13.5, letterSpacing: -0.1),
        unselectedLabelStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        indicatorColor: isDark ? const Color(0xFF431407) : AppColors.brandTint,
        height: 68,
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
        iconTheme: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return const IconThemeData(color: AppColors.brand, size: 23);
          }
          return IconThemeData(color: muted, size: 22);
        }),
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return const TextStyle(
              color: AppColors.brandDark,
              fontWeight: FontWeight.w800,
              fontSize: 11.5,
              letterSpacing: -0.2,
            );
          }
          return TextStyle(
            color: muted,
            fontWeight: FontWeight.w600,
            fontSize: 11.5,
            letterSpacing: -0.2,
          );
        }),
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(
        color: AppColors.brand,
        linearTrackColor: isDark ? const Color(0xFF1E293B) : AppColors.line,
        linearMinHeight: 7,
        borderRadius: BorderRadius.circular(999),
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) return Colors.white;
          return null;
        }),
        trackColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) return AppColors.brand;
          return null;
        }),
      ),
      checkboxTheme: CheckboxThemeData(
        fillColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) return AppColors.brand;
          return null;
        }),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(5)),
      ),
      sliderTheme: SliderThemeData(
        activeTrackColor: AppColors.brand,
        thumbColor: AppColors.brand,
        inactiveTrackColor: isDark ? const Color(0xFF1E293B) : AppColors.line,
        overlayColor: AppColors.brand.withValues(alpha: 0.12),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: AppColors.ink,
        contentTextStyle: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600, fontSize: 13.5),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: BorderSide(color: line),
        ),
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(22)),
        ),
      ),
      listTileTheme: ListTileThemeData(
        iconColor: muted,
        titleTextStyle: textTheme.titleSmall,
        subtitleTextStyle: textTheme.bodySmall,
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 2),
      ),
    );
  }
}
