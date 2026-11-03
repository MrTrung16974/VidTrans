// Renders the launcher-icon sources from the in-app VidTrans mark.
// Run: flutter test tool/generate_app_icons_test.dart && dart run flutter_launcher_icons
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vidtrans_mobile/core/widgets/studio_widgets.dart';

const _size = 1024.0;
const _gradient = LinearGradient(
  begin: Alignment.topLeft,
  end: Alignment.bottomRight,
  colors: [Color(0xFFFF985A), Color(0xFFF16B3B), Color(0xFFD94C22)],
  stops: [0.0, 0.52, 1.0],
);

Future<void> _save(String name, void Function(Canvas canvas) paint) async {
  final recorder = ui.PictureRecorder();
  paint(Canvas(recorder));
  final image = await recorder.endRecording().toImage(_size.toInt(), _size.toInt());
  final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
  File('assets/icon/$name').writeAsBytesSync(bytes!.buffer.asUint8List());
}

void _background(Canvas canvas) {
  const rect = Rect.fromLTWH(0, 0, _size, _size);
  canvas.drawRect(rect, Paint()..shader = _gradient.createShader(rect));
}

/// Draws the 64-unit mark so its tile (x/y 4..60) spans [tile] pixels, centred.
void _mark(Canvas canvas, {required double tile, bool drawTile = false}) {
  final scale = tile / 56;
  canvas.save();
  canvas.translate(_size / 2 - 32 * scale, _size / 2 - 32 * scale);
  VidTransLogoPainter(drawTile: drawTile).paint(canvas, Size.square(64 * scale));
  canvas.restore();
}

void main() {
  test('generate launcher icon sources', () async {
    await TestWidgetsFlutterBinding.ensureInitialized().runAsync(() async {
      // Legacy Android: the mark exactly as in the app (rounded tile).
      await _save('app_icon.png', (c) => _mark(c, tile: _size * 56 / 64, drawTile: true));
      // iOS rounds the corners itself and refuses transparency: full-bleed tile.
      await _save('app_icon_ios.png', (c) {
        _background(c);
        _mark(c, tile: _size * 0.84);
      });
      // Android 8+ adaptive icon: flutter_launcher_icons insets the foreground by
      // 16% per side, so the tile ends up ~60dp of the 108dp layer (safe zone).
      await _save('adaptive_background.png', _background);
      await _save('adaptive_foreground.png', (c) => _mark(c, tile: _size * 0.82));
    });
  });
}
