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
  const DownloadPolicy(this.platforms);

  /// Used when the policy cannot be loaded: everything goes to the server, as before.
  static const serverOnly = DownloadPolicy({});

  final Map<String, DownloadMode> platforms;

  DownloadMode modeFor(String? platform) =>
      platforms[platform] ?? DownloadMode.server;

  bool onPhone(String url) =>
      modeFor(socialPlatformOf(url)) != DownloadMode.server;

  factory DownloadPolicy.fromJson(Map<String, dynamic> json) => DownloadPolicy({
    for (final entry in (json['platforms'] as Map? ?? const {}).entries)
      if (entry.value is Map)
        entry.key.toString(): DownloadMode.parse((entry.value as Map)['mode']),
  });
}

const _platformDomains = {
  'Douyin': ['douyin.com', 'iesdouyin.com'],
  'TikTok': ['tiktok.com'],
  'YouTube': ['youtube.com', 'youtu.be', 'youtube-nocookie.com'],
  'Facebook': ['facebook.com', 'fb.com', 'fb.watch'],
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
