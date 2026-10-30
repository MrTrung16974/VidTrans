import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_client.dart';
import '../../../core/api/providers.dart';
import '../../../core/config/env.dart';
import '../domain/social_models.dart';

final socialRepositoryProvider = Provider<SocialRepository>((ref) => SocialRepository(ref.watch(apiClientProvider)));

class SocialRepository {
  SocialRepository(this._api);

  final ApiClient _api;

  Future<SocialAuthStatus> authStatus(SocialPlatform platform) async =>
      SocialAuthStatus.fromJson(await _api.get<Map<String, dynamic>>('/api/v1/${platform.slug}-auth'));

  /// OAuth page to open in the browser; the server sends the user back to the
  /// app deep link (allow-listed in VIDTRANS_APP_REDIRECT_URIS).
  Future<Uri> connectUrl(SocialPlatform platform) async {
    final data = await _api.get<Map<String, dynamic>>(
      '/api/v1/${platform.slug}-auth/connect',
      query: {'return_to': Env.oauthRedirect},
    );
    return Uri.parse(data['authorization_url'] as String);
  }

  /// Same split as the web: encrypted connections are revoked through
  /// `/social-connections`, legacy accounts through the platform route.
  Future<void> disconnect(SocialPlatform platform, SocialAccount account) async {
    if (account.connectionId != null) {
      await _api.delete<dynamic>('/api/v1/social-connections/${Uri.encodeComponent(account.connectionId!)}');
    } else {
      await _api.delete<dynamic>(
        '/api/v1/${platform.slug}-auth?account_id=${Uri.encodeQueryComponent(account.accountId)}',
      );
    }
  }

  Future<SocialDraft> draft(String jobId) async =>
      SocialDraft.fromJson(await _api.get<Map<String, dynamic>>('/api/v1/jobs/$jobId/tiktok-draft'));

  /// Caption suggested from the translation; a preview, nothing is saved.
  Future<String> suggestCaption(String jobId) async {
    final data = await _api.post<Map<String, dynamic>>('/api/v1/jobs/$jobId/tiktok-caption');
    return data['caption'] as String? ?? '';
  }

  Future<List<PublishAttempt>> attempts(String jobId) async {
    final data = await _api.get<Map<String, dynamic>>('/api/v1/jobs/$jobId/social-publishes');
    return [
      for (final item in (data['items'] as List? ?? const []))
        PublishAttempt.fromJson(Map<String, dynamic>.from(item as Map)),
    ];
  }

  Future<PublishAttempt> publish(String jobId, Map<String, dynamic> payload) async => PublishAttempt.fromJson(
        await _api.post<Map<String, dynamic>>('/api/v1/jobs/$jobId/social-publishes', data: payload),
      );

  /// Asks the platform for news when it has a remote id, like the web.
  Future<PublishAttempt> refresh(PublishAttempt attempt) async {
    final path = '/api/v1/social-publishes/${attempt.id}';
    final data = attempt.hasRemote
        ? await _api.post<Map<String, dynamic>>('$path/refresh')
        : await _api.get<Map<String, dynamic>>(path);
    return PublishAttempt.fromJson(data);
  }

  Future<PublishAttempt> cancel(String attemptId) async => PublishAttempt.fromJson(
        await _api.post<Map<String, dynamic>>('/api/v1/social-publishes/$attemptId/cancel'),
      );
}
