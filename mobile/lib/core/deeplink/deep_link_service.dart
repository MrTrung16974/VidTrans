import 'dart:async';

import 'package:app_links/app_links.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Links the app understands:
///   vidtrans://oauth?tiktok=connected        OAuth started by the app finished
///   vidtrans://oauth?tiktok_error=REASON     ...or failed
///   vidtrans://jobs/JOB_ID                   open a job
sealed class DeepLink {
  const DeepLink();

  static DeepLink? parse(Uri uri) {
    if (uri.scheme != 'vidtrans') return null;
    switch (uri.host) {
      case 'oauth':
        for (final entry in uri.queryParameters.entries) {
          if (entry.key.endsWith('_error')) {
            return OAuthResult(platform: entry.key.substring(0, entry.key.length - 6), error: entry.value);
          }
          if (entry.value == 'connected') return OAuthResult(platform: entry.key);
        }
        return null;
      case 'jobs':
        final segments = uri.pathSegments.where((s) => s.isNotEmpty).toList();
        return segments.isEmpty ? null : JobLink(segments.first);
    }
    return null;
  }
}

class OAuthResult extends DeepLink {
  const OAuthResult({required this.platform, this.error});

  final String platform;
  final String? error;

  bool get connected => error == null;
}

class JobLink extends DeepLink {
  const JobLink(this.jobId);

  final String jobId;
}

final deepLinkServiceProvider = Provider<DeepLinkService>((ref) {
  final service = DeepLinkService();
  ref.onDispose(service.dispose);
  return service;
});

class DeepLinkService {
  DeepLinkService([AppLinks? appLinks]) : _appLinks = appLinks ?? AppLinks();

  final AppLinks _appLinks;
  final _links = StreamController<DeepLink>.broadcast();
  StreamSubscription<Uri>? _subscription;

  /// Emits links that opened or reached the app, including the launch link.
  Stream<DeepLink> get links {
    _subscription ??= _appLinks.uriLinkStream.listen((uri) {
      final link = DeepLink.parse(uri);
      if (link != null) {
        _links.add(link);
      } else {
        debugPrint('Ignored deep link $uri');
      }
    });
    return _links.stream;
  }

  void dispose() {
    _subscription?.cancel();
    _links.close();
  }
}
