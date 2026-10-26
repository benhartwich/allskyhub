// The allskyhub look in the app, after packages/design (tokens.css, base.css): always dark,
// the night sky behind every page, glassy cards, pill buttons, Space Grotesk for titles.

import 'dart:math' as math;

import 'package:flutter/cupertino.dart' show CupertinoPageTransitionsBuilder;
import 'package:flutter/material.dart';

/// tokens.css
abstract final class Sky {
  static const bg = Color(0xFF04060F);
  static const surface = Color(0x9E111830);
  static const surface2 = Color(0x8C1C2648);
  static const line = Color(0x2E7AA2F7);
  static const text = Color(0xFFEEF1FB);
  static const text2 = Color(0xFFA7B0CC);
  static const accent = Color(0xFF7AA2F7);
  static const accent2 = Color(0xFFB48CFF);
  static const aurora = Color(0xFF4FE3B0);
  static const ink = Color(0xFF050816);
  static const warn = Color(0xFFF2C06B);
  static const danger = Color(0xFFFF7A93);
  static const display = 'SpaceGrotesk';
  static const radius = 20.0;
  static const sheen = LinearGradient(
    colors: [Color(0xFF8FE3FF), accent, accent2],
    stops: [0, .45, 1],
  );
}

ThemeData skyTheme() {
  const scheme = ColorScheme.dark(
    primary: Sky.accent,
    onPrimary: Sky.ink,
    primaryContainer: Color(0xFF243766),
    onPrimaryContainer: Sky.text,
    secondary: Sky.accent2,
    onSecondary: Sky.ink,
    secondaryContainer: Color(0xFF33295A),
    onSecondaryContainer: Sky.text,
    tertiary: Sky.aurora,
    onTertiary: Sky.ink,
    error: Sky.danger,
    onError: Sky.ink,
    surface: Color(0xFF0B1124),
    onSurface: Sky.text,
    onSurfaceVariant: Sky.text2,
    surfaceContainerLowest: Color(0xFF070B18),
    surfaceContainerLow: Color(0xFF0D1428),
    surfaceContainer: Color(0xFF111A33),
    surfaceContainerHigh: Color(0xFF16203D),
    surfaceContainerHighest: Color(0xFF1C2648),
    outline: Color(0x667AA2F7),
    outlineVariant: Sky.line,
  );
  final base = ThemeData(colorScheme: scheme, useMaterial3: true);
  final text = base.textTheme.apply(
    bodyColor: Sky.text,
    displayColor: Sky.text,
  );
  TextStyle? display(TextStyle? s, FontWeight w) =>
      s?.copyWith(fontFamily: Sky.display, fontWeight: w, letterSpacing: -.2);
  const pill = StadiumBorder();
  const buttonSize = Size(48, 48);
  final buttonText = const TextStyle(fontWeight: FontWeight.w700, fontSize: 15);
  final field = OutlineInputBorder(
    borderRadius: BorderRadius.circular(12),
    borderSide: const BorderSide(color: Sky.line),
  );
  return base.copyWith(
    scaffoldBackgroundColor: Colors.transparent,
    canvasColor: scheme.surface,
    textTheme: text.copyWith(
      displayLarge: display(text.displayLarge, FontWeight.w700),
      displayMedium: display(text.displayMedium, FontWeight.w700),
      displaySmall: display(text.displaySmall, FontWeight.w700),
      headlineLarge: display(text.headlineLarge, FontWeight.w700),
      headlineMedium: display(text.headlineMedium, FontWeight.w700),
      headlineSmall: display(text.headlineSmall, FontWeight.w600),
      titleLarge: display(text.titleLarge, FontWeight.w600),
      titleMedium: display(text.titleMedium, FontWeight.w600),
    ),
    pageTransitionsTheme: const PageTransitionsTheme(
      builders: {
        TargetPlatform.android: SkyPageTransitions(
          FadeForwardsPageTransitionsBuilder(),
        ),
        TargetPlatform.iOS: SkyPageTransitions(
          CupertinoPageTransitionsBuilder(),
        ),
      },
    ),
    appBarTheme: AppBarTheme(
      backgroundColor: const Color(0x8C04060F),
      surfaceTintColor: Colors.transparent,
      foregroundColor: Sky.text,
      elevation: 0,
      scrolledUnderElevation: 0,
      shape: const Border(bottom: BorderSide(color: Sky.line)),
      titleTextStyle: const TextStyle(
        fontFamily: Sky.display,
        fontWeight: FontWeight.w700,
        fontSize: 21,
        letterSpacing: -.4,
        color: Sky.text,
      ),
    ),
    cardTheme: CardThemeData(
      color: Sky.surface,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(Sky.radius),
        side: const BorderSide(color: Sky.line),
      ),
    ),
    listTileTheme: const ListTileThemeData(
      iconColor: Sky.text2,
      subtitleTextStyle: TextStyle(color: Sky.text2, fontSize: 14),
    ),
    dividerTheme: const DividerThemeData(color: Sky.line, space: 1),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: Sky.accent,
        foregroundColor: Sky.ink,
        minimumSize: buttonSize,
        padding: const EdgeInsets.symmetric(horizontal: 22),
        shape: pill,
        textStyle: buttonText,
      ),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: Sky.accent,
        foregroundColor: Sky.ink,
        minimumSize: buttonSize,
        padding: const EdgeInsets.symmetric(horizontal: 22),
        shape: pill,
        textStyle: buttonText,
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: Sky.text,
        backgroundColor: const Color(0x337AA2F7),
        side: const BorderSide(color: Color(0x737AA2F7)),
        minimumSize: buttonSize,
        padding: const EdgeInsets.symmetric(horizontal: 18),
        shape: pill,
        textStyle: buttonText.copyWith(fontWeight: FontWeight.w600),
      ).copyWith(iconColor: const WidgetStatePropertyAll(Sky.accent)),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: Sky.accent,
        minimumSize: const Size(44, 44),
        shape: pill,
        textStyle: buttonText.copyWith(fontWeight: FontWeight.w600),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: const Color(0x99060A18),
      border: field,
      enabledBorder: field,
      focusedBorder: field.copyWith(
        borderSide: const BorderSide(color: Sky.accent, width: 2),
      ),
      errorBorder: field.copyWith(
        borderSide: const BorderSide(color: Sky.danger),
      ),
      labelStyle: const TextStyle(color: Sky.text2),
    ),
    chipTheme: base.chipTheme.copyWith(
      backgroundColor: Sky.surface2,
      side: const BorderSide(color: Sky.line),
      shape: const StadiumBorder(),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: const Color(0xFF0D1428),
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(Sky.radius),
        side: const BorderSide(color: Sky.line),
      ),
    ),
    bottomSheetTheme: const BottomSheetThemeData(
      backgroundColor: Color(0xFF0D1428),
      surfaceTintColor: Colors.transparent,
    ),
    snackBarTheme: const SnackBarThemeData(
      backgroundColor: Color(0xFF1C2648),
      contentTextStyle: TextStyle(color: Sky.text),
      behavior: SnackBarBehavior.floating,
    ),
    progressIndicatorTheme: const ProgressIndicatorThemeData(color: Sky.accent),
  );
}

/// Every page gets its own sky, so the pages (with transparent scaffolds) stay opaque
/// during a transition.
class SkyPageTransitions extends PageTransitionsBuilder {
  const SkyPageTransitions(this.inner);

  final PageTransitionsBuilder inner;

  @override
  Widget buildTransitions<T>(
    PageRoute<T> route,
    BuildContext context,
    Animation<double> animation,
    Animation<double> secondaryAnimation,
    Widget child,
  ) => inner.buildTransitions(
    route,
    context,
    animation,
    secondaryAnimation,
    SkyBackground(child: child),
  );
}

/// base.css `body::before`: aurora glow over a dark gradient, with stars.
class SkyBackground extends StatelessWidget {
  const SkyBackground({required this.child, super.key});

  final Widget child;

  @override
  Widget build(BuildContext context) => CustomPaint(
    painter: const _SkyPainter(),
    isComplex: true,
    // The sky reaches under the system bar at the bottom (Android's edge-to-edge, the
    // iPhone's home indicator), the page itself stops above it.
    child: SafeArea(top: false, left: false, right: false, child: child),
  );
}

class _SkyPainter extends CustomPainter {
  const _SkyPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    canvas.drawRect(
      rect,
      Paint()
        ..shader = const LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [Color(0xFF060A1C), Sky.bg, Color(0xFF02030A)],
          stops: [0, .55, 1],
        ).createShader(rect),
    );
    void glow(Offset centre, double rx, double ry, Color color) {
      canvas.save();
      canvas.translate(centre.dx, centre.dy);
      canvas.scale(1, ry / rx);
      final r = Rect.fromCircle(center: Offset.zero, radius: rx);
      canvas.drawRect(
        Rect.fromLTRB(-rx, -rx, rx, rx),
        Paint()
          ..shader = RadialGradient(
            colors: [color, color.withValues(alpha: 0)],
            stops: const [0, .6],
          ).createShader(r),
      );
      canvas.restore();
    }

    final w = size.width, h = size.height;
    glow(Offset(w * .15, -h * .05), w * .8, h * .45, const Color(0x384FE3B0));
    glow(Offset(w * .85, 0), w * .7, h * .5, const Color(0x3DB48CFF));
    glow(Offset(w * .5, h * 1.1), w * 1.2, h * .6, const Color(0x592846AA));

    // Stars: the same field on every page, about one per 2 500 px².
    final random = math.Random(7);
    final star = Paint();
    final count = (w * h / 2500).round().clamp(80, 900);
    for (var i = 0; i < count; i++) {
      final p = Offset(random.nextDouble() * w, random.nextDouble() * h);
      final bright = random.nextDouble();
      star.color = Colors.white.withValues(alpha: .25 + .6 * bright * bright);
      canvas.drawCircle(p, .4 + 1.0 * bright * bright, star);
    }
  }

  @override
  bool shouldRepaint(_SkyPainter oldDelegate) => false;
}
