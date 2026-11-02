import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app/app.dart';
// Keeps the floating screen translator's background entry point
// (screenTranslatorMain) in release builds.
// ignore: unused_import
import 'features/screen_translator/screen_translator_engine.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const ProviderScope(child: VidTransApp()));
}
