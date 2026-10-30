import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/api/providers.dart';

/// Image served by the API behind `Authorization: Bearer` (logo, template...).
class AuthImage extends ConsumerWidget {
  const AuthImage(this.path, {super.key, this.fit = BoxFit.contain, this.width, this.height, this.opacity = 1});

  /// Server path such as `/api/v1/me/branding/logo?v=...`.
  final String path;
  final BoxFit fit;
  final double? width;
  final double? height;
  final double opacity;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final api = ref.watch(apiClientProvider);
    return FutureBuilder<Map<String, String>>(
      future: api.authHeaders(),
      builder: (context, snapshot) {
        final headers = snapshot.data;
        if (headers == null) return SizedBox(width: width, height: height);
        return Opacity(
          opacity: opacity,
          child: Image.network(
            api.resolve(path).toString(),
            headers: headers,
            fit: fit,
            width: width,
            height: height,
            errorBuilder: (_, _, _) => const Icon(Icons.broken_image_outlined),
          ),
        );
      },
    );
  }
}
