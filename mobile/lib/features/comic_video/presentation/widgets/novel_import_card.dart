import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../data/comic_repository.dart';

/// Interactive Qidian novel chapter importer matching web `.novel-import`.
class NovelImportCard extends ConsumerStatefulWidget {
  const NovelImportCard({
    super.key,
    required this.onImported,
  });

  /// Called when novel chapters are fetched and split into episodes.
  final void Function(
    String bookTitle,
    String episode1Text,
    List<Map<String, dynamic>> episodes,
  ) onImported;

  @override
  ConsumerState<NovelImportCard> createState() => _NovelImportCardState();
}

class _NovelImportCardState extends ConsumerState<NovelImportCard> {
  bool _expanded = false;
  late final TextEditingController _urlController;
  bool _loadingCatalog = false;
  Map<String, dynamic>? _catalog;
  int _fromChapter = 1;
  int _toChapter = 10;
  bool _skipLocked = true;
  bool _rightsConfirmed = false;
  String? _importId;
  bool _importing = false;
  int _doneChapters = 0;
  int _totalChapters = 0;
  String? _error;
  String? _successMessage;
  Timer? _pollTimer;

  static const int _maxChapters = 200;

  @override
  void initState() {
    super.initState();
    _urlController = TextEditingController();
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    _urlController.dispose();
    super.dispose();
  }

  List<Map<String, dynamic>> get _rawChapters {
    final list = _catalog?['chapters'];
    if (list is! List) return const [];
    return [
      for (final item in list)
        if (item is Map) Map<String, dynamic>.from(item),
    ];
  }

  List<Map<String, dynamic>> _selectedChapters() {
    final all = _rawChapters;
    if (all.isEmpty) return const [];
    final from = _fromChapter.clamp(1, all.length);
    final to = _toChapter.clamp(from, all.length);
    final slice = all.sublist(from - 1, to);
    if (_skipLocked) {
      return slice.where((c) => c['access'] == 'accessible').toList();
    }
    return slice;
  }

  Future<void> _loadCatalog() async {
    final url = _urlController.text.trim();
    if (url.isEmpty) {
      setState(() => _error = 'Hãy dán link truyện Qidian để tải mục lục');
      return;
    }
    FocusScope.of(context).unfocus();
    setState(() {
      _loadingCatalog = true;
      _error = null;
      _successMessage = null;
    });

    try {
      final repo = ref.read(comicRepositoryProvider);
      final catalog = await repo.novelCatalog(url);
      if (!mounted) return;
      final chapters = catalog['chapters'] as List? ?? [];
      final total = chapters.length;
      setState(() {
        _catalog = catalog;
        _fromChapter = 1;
        _toChapter = min(total > 0 ? total : 10, 10);
        _loadingCatalog = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = errorMessage(e);
        _loadingCatalog = false;
      });
    }
  }

  Future<void> _startImport() async {
    if (!_rightsConfirmed) {
      setState(() => _error = 'Hãy xác nhận bạn là tác giả hoặc đã được phép chuyển thể truyện này');
      return;
    }
    final selected = _selectedChapters();
    if (selected.isEmpty) {
      setState(() => _error = 'Không có chương nào đọc được trong khoảng đã chọn');
      return;
    }
    if (selected.length > _maxChapters) {
      setState(() => _error = 'Mỗi lần chỉ tải tối đa $_maxChapters chương');
      return;
    }

    final chapterIds = [
      for (final c in selected) (c['id'] ?? c['chapter_id'] ?? '').toString(),
    ].where((id) => id.isNotEmpty).toList();

    FocusScope.of(context).unfocus();
    setState(() {
      _importing = true;
      _error = null;
      _doneChapters = 0;
      _totalChapters = selected.length;
    });

    try {
      final repo = ref.read(comicRepositoryProvider);
      final url = _urlController.text.trim();
      final res = await repo.startNovelImport(url, chapterIds, rightsConfirmed: true);
      _importId = res['import_id']?.toString();
      _pollTimer = Timer.periodic(const Duration(seconds: 3), (_) => _pollStatus());
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _importing = false;
        _error = errorMessage(e);
      });
    }
  }

  Future<void> _pollStatus() async {
    final id = _importId;
    if (id == null) return;
    try {
      final repo = ref.read(comicRepositoryProvider);
      final status = await repo.novelImportStatus(id);
      if (!mounted) return;

      final state = status['status']?.toString();
      final done = (status['done'] as num?)?.toInt() ?? 0;
      final total = (status['total'] as num?)?.toInt() ?? _totalChapters;

      setState(() {
        _doneChapters = done;
        _totalChapters = total;
      });

      if (state == 'completed') {
        _pollTimer?.cancel();
        _pollTimer = null;
        // Fetch full text with include_text=true
        final full = await repo.novelImportStatus(id, includeText: true);
        if (!mounted) return;

        final rawEpisodes = full['episodes'] as List? ?? [];
        final episodes = [
          for (final ep in rawEpisodes)
            if (ep is Map) Map<String, dynamic>.from(ep),
        ];

        final bookTitle = _catalog?['title']?.toString() ?? 'Truyện mới';
        final episode1Text = episodes.isNotEmpty ? (episodes[0]['text']?.toString() ?? '') : '';

        setState(() {
          _importing = false;
          _successMessage = 'Đã tải thành công ${episodes.length} tập ($done chương)!';
        });

        HapticFeedback.mediumImpact();
        widget.onImported(bookTitle, episode1Text, episodes);
      } else if (state == 'failed' || state == 'cancelled') {
        _pollTimer?.cancel();
        _pollTimer = null;
        setState(() {
          _importing = false;
          _error = state == 'cancelled' ? 'Lượt tải truyện đã bị hủy' : (status['error']?.toString() ?? 'Tải chương thất bại');
        });
      }
    } catch (e) {
      // transient network glitch during polling, keep trying
    }
  }

  Future<void> _cancelImport() async {
    final id = _importId;
    _pollTimer?.cancel();
    _pollTimer = null;
    if (id != null) {
      try {
        await ref.read(comicRepositoryProvider).cancelNovelImport(id);
      } catch (_) {}
    }
    if (mounted) {
      setState(() {
        _importing = false;
        _error = 'Đã dừng tải chương';
      });
    }
  }

  Future<void> _pasteFromClipboard() async {
    HapticFeedback.selectionClick();
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text?.trim() ?? '';
    if (text.isNotEmpty) {
      _urlController.text = text;
      _loadCatalog();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 14),
      decoration: BoxDecoration(
        color: const Color(0xFFFFFDF9),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: _expanded ? const Color(0xFFFDBA74) : const Color(0xFFFED7AA),
          width: 1.5,
        ),
        boxShadow: const [
          BoxShadow(
            color: Color(0x0DEA580C),
            blurRadius: 10,
            offset: Offset(0, 3),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Accordion Summary Header
          Material(
            color: Colors.transparent,
            child: InkWell(
              borderRadius: BorderRadius.circular(16),
              onTap: () {
                HapticFeedback.selectionClick();
                setState(() => _expanded = !_expanded);
              },
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                child: Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3.5),
                      decoration: BoxDecoration(
                        color: const Color(0xFFFFEDD5),
                        borderRadius: BorderRadius.circular(999),
                        border: Border.all(color: const Color(0xFFFDBA74), width: 1),
                      ),
                      child: const Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text('📖', style: TextStyle(fontSize: 11)),
                          SizedBox(width: 4),
                          Text(
                            'Qidian',
                            style: TextStyle(
                              fontSize: 10.5,
                              fontWeight: FontWeight.w800,
                              color: Color(0xFFC2410C),
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 10),
                    const Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'Nhập chương từ link truyện (Qidian)',
                            style: TextStyle(
                              fontSize: 12.8,
                              fontWeight: FontWeight.w800,
                              color: Color(0xFF9A3412),
                            ),
                          ),
                          Text(
                            'Tự động tải chương, chia tập & điền kịch bản',
                            style: TextStyle(
                              fontSize: 11,
                              color: AppColors.muted,
                              fontWeight: FontWeight.w500,
                            ),
                          ),
                        ],
                      ),
                    ),
                    Icon(
                      _expanded ? Icons.keyboard_arrow_up_rounded : Icons.keyboard_arrow_down_rounded,
                      color: const Color(0xFFEA580C),
                      size: 22,
                    ),
                  ],
                ),
              ),
            ),
          ),

          // Body Content (when expanded)
          if (_expanded) ...[
            const Divider(height: 1, thickness: 1, color: Color(0xFFFFEDD5)),
            Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  // URL input + Action buttons
                  Row(
                    children: [
                      Expanded(
                        child: TextField(
                          controller: _urlController,
                          enabled: !_loadingCatalog && !_importing,
                          decoration: InputDecoration(
                            hintText: 'https://www.qidian.com/book/1045928363/',
                            hintStyle: const TextStyle(fontSize: 12, color: Color(0xFF94A3B8)),
                            isDense: true,
                            contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                            border: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(10),
                              borderSide: const BorderSide(color: Color(0xFFCBD5E1)),
                            ),
                            suffixIcon: _urlController.text.isNotEmpty
                                ? IconButton(
                                    icon: const Icon(Icons.clear_rounded, size: 16),
                                    onPressed: () => setState(() => _urlController.clear()),
                                  )
                                : IconButton(
                                    icon: const Icon(Icons.paste_rounded, size: 16, color: Color(0xFFEA580C)),
                                    tooltip: 'Dán link từ bộ nhớ tạm',
                                    onPressed: _pasteFromClipboard,
                                  ),
                          ),
                          onSubmitted: (_) => _loadCatalog(),
                        ),
                      ),
                      const SizedBox(width: 8),
                      SizedBox(
                        height: 38,
                        child: OutlinedButton(
                          style: OutlinedButton.styleFrom(
                            foregroundColor: const Color(0xFFC2410C),
                            side: const BorderSide(color: Color(0xFFFDBA74)),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                            padding: const EdgeInsets.symmetric(horizontal: 12),
                          ),
                          onPressed: _loadingCatalog || _importing ? null : _loadCatalog,
                          child: _loadingCatalog
                              ? const SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(strokeWidth: 2, color: Color(0xFFEA580C)),
                                )
                              : const Text(
                                  'Tải mục lục',
                                  style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
                                ),
                        ),
                      ),
                    ],
                  ),

                  // Error notification
                  if (_error != null) ...[
                    const SizedBox(height: 10),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
                      decoration: BoxDecoration(
                        color: const Color(0xFFFEF2F2),
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: const Color(0xFFFECACA)),
                      ),
                      child: Text(
                        _error!,
                        style: const TextStyle(fontSize: 11.5, color: Color(0xFFDC2626), fontWeight: FontWeight.w600),
                      ),
                    ),
                  ],

                  // Success notification
                  if (_successMessage != null) ...[
                    const SizedBox(height: 10),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
                      decoration: BoxDecoration(
                        color: const Color(0xFFF0FDF4),
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: const Color(0xFFBBF7D0)),
                      ),
                      child: Row(
                        children: [
                          const Icon(Icons.check_circle_rounded, size: 16, color: Color(0xFF16A34A)),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Text(
                              _successMessage!,
                              style: const TextStyle(fontSize: 11.5, color: Color(0xFF15803D), fontWeight: FontWeight.w700),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],

                  // Catalog details & Chapter selection
                  if (_catalog != null) ...[
                    const SizedBox(height: 12),
                    Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: Colors.white,
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(color: const Color(0xFFE2E8F0)),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          // Book name + Author
                          Row(
                            children: [
                              const Icon(Icons.menu_book_rounded, size: 18, color: Color(0xFFEA580C)),
                              const SizedBox(width: 8),
                              Expanded(
                                child: Text(
                                  '${_catalog!['title'] ?? 'Truyện'}'
                                  '${_catalog!['author'] != null ? ' · ${_catalog!['author']}' : ''}',
                                  style: const TextStyle(
                                    fontSize: 13,
                                    fontWeight: FontWeight.w800,
                                    color: Color(0xFF0F172A),
                                  ),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 4),
                          Text(
                            'Tổng ${_rawChapters.length} chương · ${_rawChapters.where((c) => c['access'] == 'accessible').length} chương đọc được',
                            style: const TextStyle(fontSize: 11, color: AppColors.muted),
                          ),
                          const SizedBox(height: 10),

                          // From chapter / To chapter
                          Row(
                            children: [
                              Expanded(
                                child: Row(
                                  children: [
                                    const Text('Từ: ', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
                                    Expanded(
                                      child: SizedBox(
                                        height: 34,
                                        child: TextFormField(
                                          initialValue: '$_fromChapter',
                                          keyboardType: TextInputType.number,
                                          enabled: !_importing,
                                          textAlign: TextAlign.center,
                                          style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700),
                                          decoration: InputDecoration(
                                            isDense: true,
                                            contentPadding: const EdgeInsets.symmetric(vertical: 6, horizontal: 4),
                                            border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
                                          ),
                                          onChanged: (v) {
                                            final val = int.tryParse(v);
                                            if (val != null) setState(() => _fromChapter = val);
                                          },
                                        ),
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                              const SizedBox(width: 12),
                              Expanded(
                                child: Row(
                                  children: [
                                    const Text('Đến: ', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
                                    Expanded(
                                      child: SizedBox(
                                        height: 34,
                                        child: TextFormField(
                                          initialValue: '$_toChapter',
                                          keyboardType: TextInputType.number,
                                          enabled: !_importing,
                                          textAlign: TextAlign.center,
                                          style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700),
                                          decoration: InputDecoration(
                                            isDense: true,
                                            contentPadding: const EdgeInsets.symmetric(vertical: 6, horizontal: 4),
                                            border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
                                          ),
                                          onChanged: (v) {
                                            final val = int.tryParse(v);
                                            if (val != null) setState(() => _toChapter = val);
                                          },
                                        ),
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 8),

                          // Skip locked checkbox
                          Row(
                            children: [
                              SizedBox(
                                width: 22,
                                height: 22,
                                child: Checkbox(
                                  value: _skipLocked,
                                  activeColor: const Color(0xFFEA580C),
                                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(4)),
                                  onChanged: _importing ? null : (v) => setState(() => _skipLocked = v ?? true),
                                ),
                              ),
                              const SizedBox(width: 8),
                              const Expanded(
                                child: Text(
                                  'Bỏ qua chương bị khóa (VIP chưa mua)',
                                  style: TextStyle(fontSize: 11.5, color: Color(0xFF475569), fontWeight: FontWeight.w600),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 4),

                          // Rights consent checkbox
                          Row(
                            children: [
                              SizedBox(
                                width: 22,
                                height: 22,
                                child: Checkbox(
                                  value: _rightsConfirmed,
                                  activeColor: const Color(0xFFEA580C),
                                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(4)),
                                  onChanged: _importing ? null : (v) => setState(() => _rightsConfirmed = v ?? false),
                                ),
                              ),
                              const SizedBox(width: 8),
                              const Expanded(
                                child: Text(
                                  'Tôi là tác giả, hoặc đã được tác giả/đơn vị giữ bản quyền (ví dụ Yuewen/Qidian, Jinjiang) '
                                  'cho phép chuyển thể truyện này thành video, và tự chịu trách nhiệm về bản quyền '
                                  '(Điều khoản mục 4).',
                                  style: TextStyle(fontSize: 11.5, color: Color(0xFF475569), fontWeight: FontWeight.w600),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 10),

                          // Summary badge
                          Builder(builder: (context) {
                            final chosen = _selectedChapters();
                            final words = chosen.fold<int>(0, (sum, c) => sum + ((c['word_count'] as num?)?.toInt() ?? 0));
                            final limit = (_catalog!['episode_chars'] as num?)?.toInt() ?? 59000;
                            final episodes = max(1, (words / limit).ceil());
                            return Text(
                              'Đã chọn ${chosen.length} chương (~${words > 0 ? words : (chosen.length * 2000)} chữ) → dự kiến chia thành $episodes tập.',
                              style: const TextStyle(fontSize: 11, color: Color(0xFFEA580C), fontWeight: FontWeight.w700),
                            );
                          }),
                          const SizedBox(height: 12),

                          // CTA Button & Progress Bar
                          if (_importing) ...[
                            ClipRRect(
                              borderRadius: BorderRadius.circular(999),
                              child: LinearProgressIndicator(
                                value: _totalChapters > 0 ? (_doneChapters / _totalChapters) : null,
                                backgroundColor: const Color(0xFFFFEDD5),
                                color: const Color(0xFFEA580C),
                                minHeight: 7,
                              ),
                            ),
                            const SizedBox(height: 8),
                            Row(
                              mainAxisAlignment: MainAxisAlignment.spaceBetween,
                              children: [
                                Text(
                                  'Đang tải chương $_doneChapters/$_totalChapters…',
                                  style: const TextStyle(fontSize: 11.5, color: Color(0xFFC2410C), fontWeight: FontWeight.w600),
                                ),
                                TextButton(
                                  onPressed: _cancelImport,
                                  child: const Text('Hủy', style: TextStyle(color: Color(0xFFEF4444), fontSize: 11.5)),
                                ),
                              ],
                            ),
                          ] else ...[
                            ElevatedButton.icon(
                              style: ElevatedButton.styleFrom(
                                backgroundColor: const Color(0xFFEA580C),
                                foregroundColor: Colors.white,
                                elevation: 0,
                                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                                padding: const EdgeInsets.symmetric(vertical: 10),
                              ),
                              onPressed: _startImport,
                              icon: const Icon(Icons.cloud_download_rounded, size: 17),
                              label: const Text(
                                'Tải chương vào truyện',
                                style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w800),
                              ),
                            ),
                          ],
                        ],
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}
