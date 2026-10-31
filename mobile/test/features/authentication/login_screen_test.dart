import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/app/theme/app_theme.dart';
import 'package:vidtrans_mobile/features/authentication/presentation/screens/login_screen.dart';

void main() {
  const viewports = <(String, Size)>[
    ('Compact Android 360x640', Size(360, 640)),
    ('iPhone SE / 8 375x667', Size(375, 667)),
    ('Standard Phone 390x844', Size(390, 844)),
    ('Large Phone 430x932', Size(430, 932)),
  ];

  for (final (name, size) in viewports) {
    testWidgets('LoginScreen fits without scrolling on $name', (tester) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1.0;
      addTearDown(() {
        tester.view.resetPhysicalSize();
        tester.view.resetDevicePixelRatio();
      });

      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: AppTheme.light(),
            home: const LoginScreen(),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
      expect(find.text('Đăng nhập VidTrans'), findsOneWidget);
      // The guest trial entry must stay reachable without scrolling.
      expect(find.text('Dùng thử'), findsOneWidget);

      final scrollable = tester.state<ScrollableState>(find.byType(Scrollable).first);
      expect(
        scrollable.position.maxScrollExtent,
        0.0,
        reason: 'LoginScreen should not require vertical scrolling on $name',
      );
    });
  }
}
