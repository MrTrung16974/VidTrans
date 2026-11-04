/// Where a social link is downloaded (`GET /api/v1/download/policy`).
///
/// Downloading on the phone uses the user's own IP and session, which the
/// platforms do not flag as a bot the way they flag the server's datacenter IP.
enum DownloadMode {
  /// On the phone; if that fails the link is handed to the server.
  clientThenServer,

  /// The server downloads it, as before.
  server;

  static DownloadMode parse(Object? value) =>
      value == 'client_then_server' || value == 'client'
      ? clientThenServer
      : server;
}

class DownloadPolicy {
  const DownloadPolicy(this.platforms, {this.blocked = const {}});

  /// Used when the policy cannot be loaded: everything goes to the server, as before.
  static const serverOnly = DownloadPolicy({});

  final Map<String, DownloadMode> platforms;

  /// Platforms whose "Tải video …" plan feature is off. Their links are not
  /// downloaded on the phone; the server refuses them with a clear message.
  final Set<String> blocked;

  bool allows(String url) => !blocked.contains(socialPlatformOf(url));

  DownloadMode modeFor(String? platform) =>
      platforms[platform] ?? DownloadMode.server;

  bool onPhone(String url) =>
      allows(url) && modeFor(socialPlatformOf(url)) != DownloadMode.server;

  factory DownloadPolicy.fromJson(Map<String, dynamic> json) {
    final entries = (json['platforms'] as Map? ?? const {}).entries
        .where((entry) => entry.value is Map);
    return DownloadPolicy(
      {
        for (final entry in entries)
          entry.key.toString(): DownloadMode.parse((entry.value as Map)['mode']),
      },
      blocked: {
        for (final entry in entries)
          if ((entry.value as Map)['allowed'] == false) entry.key.toString(),
      },
    );
  }
}

const _platformDomains = {
  'Douyin': ['douyin.com', 'iesdouyin.com'],
  'TikTok': ['tiktok.com'],
  'YouTube': ['youtube.com', 'youtu.be', 'youtube-nocookie.com'],
  'Facebook': ['facebook.com', 'fb.com', 'fb.watch'],
  // Needs the login cookies of the server's source browser.
  'Xiaohongshu': ['xiaohongshu.com', 'xhslink.com'],
  // Always downloaded by the server (it fetches the original file, not a preview).
  'Google Drive': [
    'drive.google.com',
    'docs.google.com',
    'drive.usercontent.google.com',
  ],
};

/// Platform name as the server spells it (`social_platform()` in the backend), or null.
String? socialPlatformOf(String url) {
  final host = (Uri.tryParse(url.trim())?.host ?? '').toLowerCase();
  if (host.isEmpty) return null;
  for (final entry in _platformDomains.entries) {
    if (entry.value.any(
      (domain) => host == domain || host.endsWith('.$domain'),
    ))
      return entry.key;
  }
  return null;
}
