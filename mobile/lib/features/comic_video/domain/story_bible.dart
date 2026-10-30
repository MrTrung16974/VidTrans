import 'dart:convert';

/// Editable copy of the bible proposed by `POST /comic/bible/suggest`
/// (characters, places, world), mirroring createBibleEditor in web/js/comic.js.
///
/// Entries are plain maps so fields the app does not edit (the English
/// prompts, species, voice...) are sent back untouched.
class BibleDraft {
  BibleDraft({required this.world, required this.worldVi, required this.characters, required this.locations});

  String world;
  String worldVi;
  final List<Map<String, dynamic>> characters;
  final List<Map<String, dynamic>> locations;

  factory BibleDraft.fromJson(Map<String, dynamic> json) => BibleDraft(
        world: json['world']?.toString() ?? '',
        worldVi: json['world_vi']?.toString() ?? '',
        characters: [
          for (final item in (json['characters'] as List? ?? const []))
            if (item is Map) Map<String, dynamic>.from(item),
        ],
        locations: [
          for (final item in (json['locations'] as List? ?? const []))
            if (item is Map) Map<String, dynamic>.from(item),
        ],
      );

  static const _englishTwin = {
    'appearance_vi': 'appearance',
    'outfit_vi': 'outfit',
    'description_vi': 'description',
  };

  /// Sets a field; a changed Vietnamese look clears its English prompt so the
  /// server translates it again.
  static void setField(Map<String, dynamic> item, String field, String value) {
    final english = _englishTwin[field];
    if (english != null && value != (item[field]?.toString() ?? '')) item[english] = '';
    item[field] = value;
  }

  void setWorldVi(String value) {
    if (value != worldVi) world = '';
    worldVi = value;
  }

  void addCharacter() => characters.add(
      {'name': '', 'gender': 'unknown', 'age': '', 'appearance_vi': '', 'outfit_vi': '', 'status': 'new'});

  void addLocation() => locations.add({'name': '', 'description_vi': '', 'status': 'new'});

  /// Keeps only new entries (a new episode compared with its series).
  void keepOnlyNew() {
    characters.removeWhere((item) => item['status'] != 'new');
    locations.removeWhere((item) => item['status'] != 'new');
  }

  /// Everything is new in a new story: the "Mới / Đã có" badges only matter for episodes.
  void clearStatus() {
    for (final item in [...characters, ...locations]) {
      item.remove('status');
    }
  }

  int get newCharacters => characters.where((item) => item['status'] != 'known').length;
  int get newLocations => locations.where((item) => item['status'] != 'known').length;

  /// The approved bible as the API expects it (web `collect`): unnamed entries
  /// dropped, ids made unique, `status` stripped. [withWorld] is off for
  /// episodes, whose world belongs to the series.
  Map<String, dynamic> collect({bool onlyNew = false, bool withWorld = true}) {
    final used = <String>{};
    String uniqueId(Map<String, dynamic> item, String fallback) {
      final base = asciiId(item['name']?.toString() ?? '', fallback);
      var id = (item['id']?.toString().isNotEmpty ?? false) ? item['id'].toString() : base;
      for (var n = 2; used.contains(id); n++) {
        id = '${base}_$n';
      }
      used.add(id);
      return id;
    }

    bool keep(Map<String, dynamic> item) =>
        (item['name']?.toString().trim().isNotEmpty ?? false) && (!onlyNew || item['status'] != 'known');
    Map<String, dynamic> strip(Map<String, dynamic> item) => Map.of(item)..remove('status');

    return {
      'world': withWorld ? world : '',
      'world_vi': withWorld ? worldVi.trim() : '',
      'characters': [
        for (final item in characters.where(keep))
          strip({
            ...item,
            'id': uniqueId(item, 'character'),
            'name': item['name'].toString().trim(),
            'species': (item['species']?.toString().isNotEmpty ?? false) ? item['species'] : 'human',
            'appearance': item['appearance'] ?? '',
            'outfit': item['outfit'] ?? '',
          }),
      ],
      'locations': [
        for (final item in locations.where(keep))
          strip({
            ...item,
            'id': uniqueId(item, 'place'),
            'name': item['name'].toString().trim(),
            'description': item['description'] ?? '',
          }),
      ],
    };
  }

  String toJsonString({bool onlyNew = false, bool withWorld = true}) =>
      jsonEncode(collect(onlyNew: onlyNew, withWorld: withWorld));
}

const _vietnamese = {
  'a': 'àáạảãâầấậẩẫăằắặẳẵ',
  'e': 'èéẹẻẽêềếệểễ',
  'i': 'ìíịỉĩ',
  'o': 'òóọỏõôồốộổỗơờớợởỡ',
  'u': 'ùúụủũưừứựửữ',
  'y': 'ỳýỵỷỹ',
  'd': 'đ',
};

final _foldMap = {
  for (final entry in _vietnamese.entries)
    for (final rune in entry.value.runes) String.fromCharCode(rune): entry.key,
};

/// "Lý Thanh Đình" -> "ly_thanh_dinh" (asciiId in web/js/comic.js).
String asciiId(String text, String fallback) {
  final folded = text.toLowerCase().split('').map((char) => _foldMap[char] ?? char).join();
  var id = folded.replaceAll(RegExp(r'[^a-z0-9]+'), '_').replaceAll(RegExp(r'^_+|_+$'), '');
  if (id.length > 40) id = id.substring(0, 40);
  return id.isEmpty ? fallback : id;
}
