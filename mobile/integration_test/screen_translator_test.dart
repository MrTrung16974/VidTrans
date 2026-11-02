// On-device test of the floating screen translator (phase 2), driven from the
// host by scripts/test_screen_translator.sh, which grants the overlay
// permission, accepts the screen-capture dialog, taps the floating button and
// takes screenshots. Uses the debug-only "debug-echo" translator, so it needs
// neither offline models nor a signed-in server session.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:vidtrans_mobile/features/screen_translator/screen_translator_controller.dart';
// The service's headless engine runs screenTranslatorMain from this library;
// the test (not lib/main.dart) is the program root here, so include it.
// ignore: unused_import
import 'package:vidtrans_mobile/features/screen_translator/screen_translator_engine.dart';

class _ChatPage extends StatelessWidget {
  const _ChatPage();

  static const _messages = [
    ('你今天有时间开会吗？', false),
    ('我们下午三点在会议室见面吧', true),
    ('好的，没问题', false),
  ];

  @override
  Widget build(BuildContext context) => Scaffold(
    backgroundColor: const Color(0xFFEDEDED),
    appBar: AppBar(title: const Text('微信')),
    body: ListView(
      padding: const EdgeInsets.all(16),
      children: [
        for (final (text, mine) in _messages)
          Align(
            alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
            child: Container(
              margin: const EdgeInsets.symmetric(vertical: 14),
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                color: mine ? const Color(0xFF95EC69) : Colors.white,
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                text,
                style: const TextStyle(fontSize: 18, color: Color(0xFF191919)),
              ),
            ),
          ),
      ],
    ),
  );
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('floating button captures, translates and overlays the screen', (
    tester,
  ) async {
    await tester.pumpWidget(const MaterialApp(home: _ChatPage()));
    await tester.pumpAndSettle();
    const controller = ScreenTranslatorController();

    // The host script grants "display over other apps" once the app is installed.
    var allowed = false;
    for (var i = 0; i < 30 && !allowed; i++) {
      allowed = await tester.runAsync(controller.canDrawOverlays) ?? false;
      if (!allowed) {
        await tester.runAsync(
          () => Future<void>.delayed(const Duration(seconds: 1)),
        );
      }
    }
    expect(
      allowed,
      isTrue,
      reason: 'run through scripts/test_screen_translator.sh',
    );
    debugPrint('SCREEN_TRANSLATOR: requesting capture consent');
    final started = await tester.runAsync(
      () => controller.start(source: 'auto', target: 'vi', mode: 'debug-echo'),
    );
    expect(started, isTrue);
    debugPrint('SCREEN_TRANSLATOR: started');
    // The service flags itself running once Android has started it.
    var running = false;
    for (var i = 0; i < 25 && !running; i++) {
      running = await tester.runAsync(controller.isRunning) ?? false;
      if (!running) {
        await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 200)),
        );
      }
    }
    expect(running, isTrue);

    // The host script taps the button and screenshots the overlay meanwhile.
    for (var i = 0; i < 45; i++) {
      await tester.runAsync(
        () => Future<void>.delayed(const Duration(seconds: 1)),
      );
      await tester.pump();
    }
    await tester.runAsync(controller.stop);
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(seconds: 1)),
    );
    expect(await tester.runAsync(controller.isRunning), isFalse);
    debugPrint('SCREEN_TRANSLATOR: stopped');
  });
}
