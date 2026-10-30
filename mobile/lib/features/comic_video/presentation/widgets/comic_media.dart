import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:video_player/video_player.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/errors/api_exception.dart';
import '../../data/comic_repository.dart';

/// Media served by `/api/v1/comic/.../media/...` needs the Bearer header.
final _authHeadersProvider = FutureProvider.autoDispose<Map<String, String>>(
  (ref) => ref.watch(comicRepositoryProvider).authHeaders(),
);

class AuthImage extends ConsumerWidget {
  const AuthImage(this.path, {super.key, this.fit = BoxFit.cover, this.placeholder});

  /// Server path such as `/api/v1/comic/projects/{id}/media/images/x.png`.
  final String path;
  final BoxFit fit;
  final Widget? placeholder;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final headers = ref.watch(_authHeadersProvider).valueOrNull;
    final fallback = placeholder ?? const ColoredBox(color: AppColors.surfaceMuted);
    if (headers == null) return fallback;
    return Image.network(
      ref.read(comicRepositoryProvider).mediaUri(path).toString(),
      headers: headers,
      fit: fit,
      errorBuilder: (_, _, _) => const Center(
        child: Icon(Icons.broken_image_outlined, color: AppColors.textMuted),
      ),
      loadingBuilder: (context, child, progress) => progress == null ? child : fallback,
    );
  }
}

/// Plays a clip (shot preview, animation) full screen.
Future<void> showComicClip(BuildContext context, String path) => Navigator.of(context).push(
      MaterialPageRoute<void>(fullscreenDialog: true, builder: (_) => _ClipScreen(path: path)),
    );

class _ClipScreen extends ConsumerStatefulWidget {
  const _ClipScreen({required this.path});

  final String path;

  @override
  ConsumerState<_ClipScreen> createState() => _ClipScreenState();
}

class _ClipScreenState extends ConsumerState<_ClipScreen> {
  VideoPlayerController? _controller;
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    _open();
  }

  Future<void> _open() async {
    final repo = ref.read(comicRepositoryProvider);
    final controller = VideoPlayerController.networkUrl(repo.mediaUri(widget.path), httpHeaders: await repo.authHeaders());
    try {
      await controller.initialize();
      await controller.setLooping(true);
      await controller.play();
      if (!mounted) return controller.dispose();
      setState(() => _controller = controller);
    } catch (_) {
      await controller.dispose();
      if (mounted) setState(() => _failed = true);
    }
  }

  @override
  void dispose() {
    _controller?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final controller = _controller;
    return Scaffold(
      backgroundColor: AppColors.darkBg,
      appBar: AppBar(
        backgroundColor: AppColors.darkSurface,
        foregroundColor: AppColors.darkText,
        title: const Text('Xem trước Shot', style: TextStyle(color: AppColors.darkText)),
      ),
      body: Center(
        child: _failed
            ? const Text('Không phát được clip.', style: TextStyle(color: Colors.white))
            : controller == null
                ? const CircularProgressIndicator(color: AppColors.primary)
                : AspectRatio(aspectRatio: controller.value.aspectRatio, child: VideoPlayer(controller)),
      ),
    );
  }
}

/// Picks one file; null when the user cancels.
Future<LocalFile?> pickLocalFile({required List<String> extensions}) async {
  final files = await FilePicker.pickFiles(type: FileType.custom, allowedExtensions: extensions);
  final file = files.firstOrNull;
  if (file?.path == null) return null;
  return LocalFile(path: file!.path!, name: file.name);
}

Future<LocalFile?> pickPicture() => pickLocalFile(extensions: const ['png', 'jpg', 'jpeg', 'webp']);

/// Runs an action, then shows [done] or the error in a snackbar. Returns true on success.
Future<bool> runComicAction(BuildContext context, Future<void> Function() action, {String? done}) async {
  final messenger = ScaffoldMessenger.of(context);
  try {
    await action();
    if (done != null) messenger.showSnackBar(SnackBar(content: Text(done)));
    return true;
  } catch (error) {
    messenger.showSnackBar(SnackBar(content: Text(error is FormatException ? error.message : errorMessage(error))));
    return false;
  }
}

Future<bool> confirmComic(BuildContext context, String message, {String action = 'Đồng ý'}) async {
  final ok = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      content: Text(message),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Hủy')),
        FilledButton(onPressed: () => Navigator.pop(context, true), child: Text(action)),
      ],
    ),
  );
  return ok ?? false;
}

/// Viewers may only read.
bool comicReadOnly(WidgetRef ref) => ref.watch(sessionProvider).valueOrNull?.isReadOnly ?? true;

/// A select (dropdown) with the web's labels; disabled options cannot be picked.
class ComicDropdown<T> extends StatelessWidget {
  const ComicDropdown({
    super.key,
    required this.label,
    required this.value,
    required this.items,
    required this.onChanged,
    this.enabled = true,
  });

  final String label;
  final T value;
  final List<(T value, String label, bool enabled)> items;
  final ValueChanged<T>? onChanged;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    final known = items.any((item) => item.$1 == value);
    return DropdownButtonFormField<T>(
      initialValue: known ? value : null,
      isExpanded: true,
      decoration: InputDecoration(labelText: label),
      items: [
        for (final item in items)
          DropdownMenuItem<T>(
            value: item.$1,
            enabled: item.$3,
            child: Text(
              item.$2,
              overflow: TextOverflow.ellipsis,
              style: item.$3 ? null : TextStyle(color: Theme.of(context).disabledColor),
            ),
          ),
      ],
      onChanged: enabled && onChanged != null ? (value) => value == null ? null : onChanged!(value) : null,
    );
  }
}
