import '../../jobs/domain/job.dart';

// Models for /api/v1/comic (backend/domain/comic.py, application/comic_routes.py).
// Parsing is lenient: unknown fields are ignored and missing ones get the
// backend defaults, so a newer server does not break an older app.

List<String> _strings(Object? value) => [for (final item in (value as List? ?? const [])) item.toString()];

Map<String, String> _stringMap(Object? value) => {
      for (final entry in (value as Map? ?? const {}).entries)
        if (entry.value != null) entry.key.toString(): entry.value.toString(),
    };

double _double(Object? value, double fallback) => value is num ? value.toDouble() : fallback;

int _int(Object? value, int fallback) => value is num ? value.toInt() : fallback;

class DialogueLine {
  const DialogueLine(this.character, this.text);

  final String character;
  final String text;

  Map<String, dynamic> toJson() => {'character': character, 'text': text};

  factory DialogueLine.fromJson(Map<String, dynamic> json) =>
      DialogueLine(json['character']?.toString() ?? '', json['text']?.toString() ?? '');

  static List<DialogueLine> listFrom(Object? value) => [
        for (final item in (value as List? ?? const []))
          if (item is Map) DialogueLine.fromJson(Map<String, dynamic>.from(item)),
      ];
}

/// "id: lời" per line, the text format the web editor uses.
String dialogueText(List<DialogueLine> lines) => lines.map((line) => '${line.character}: ${line.text}').join('\n');

/// Parses [dialogueText] back; throws [FormatException] with the web's message.
List<DialogueLine> parseDialogue(String value) {
  final result = <DialogueLine>[];
  for (final raw in value.split('\n')) {
    final line = raw.trim();
    if (line.isEmpty) continue;
    final index = line.indexOf(':');
    if (index < 1) throw FormatException('Dòng thoại "$line" cần dạng id_nhân_vật: lời thoại');
    result.add(DialogueLine(line.substring(0, index).trim(), line.substring(index + 1).trim()));
  }
  return result;
}

/// "a, b ,c" -> ["a", "b", "c"].
List<String> splitIds(String value) =>
    value.split(',').map((item) => item.trim()).where((item) => item.isNotEmpty).toList();

class ComicCharacter {
  const ComicCharacter({
    required this.id,
    required this.name,
    this.gender = 'unknown',
    this.species = 'human',
    this.age = '',
    this.appearance = '',
    this.outfit = '',
    this.appearanceVi = '',
    this.outfitVi = '',
    this.voice,
    this.approvedReference,
    this.referenceUrl,
  });

  final String id;
  final String name;
  final String gender;
  final String species;
  final String age;
  final String appearance;
  final String outfit;
  final String appearanceVi;
  final String outfitVi;
  final String? voice;
  final String? approvedReference;

  /// Series only: the pinned picture shared by every episode.
  final String? referenceUrl;

  factory ComicCharacter.fromJson(Map<String, dynamic> json) => ComicCharacter(
        id: json['id']?.toString() ?? '',
        name: json['name']?.toString() ?? '',
        gender: json['gender']?.toString() ?? 'unknown',
        species: json['species']?.toString() ?? 'human',
        age: json['age']?.toString() ?? '',
        appearance: json['appearance']?.toString() ?? '',
        outfit: json['outfit']?.toString() ?? '',
        appearanceVi: json['appearance_vi']?.toString() ?? '',
        outfitVi: json['outfit_vi']?.toString() ?? '',
        voice: json['voice']?.toString(),
        approvedReference: json['approved_reference']?.toString(),
        referenceUrl: json['reference_url']?.toString(),
      );
}

class ComicLocation {
  const ComicLocation({
    required this.id,
    required this.name,
    this.description = '',
    this.descriptionVi = '',
    this.approvedReference,
    this.referenceUrl,
  });

  final String id;
  final String name;
  final String description;
  final String descriptionVi;
  final String? approvedReference;
  final String? referenceUrl;

  factory ComicLocation.fromJson(Map<String, dynamic> json) => ComicLocation(
        id: json['id']?.toString() ?? '',
        name: json['name']?.toString() ?? '',
        description: json['description']?.toString() ?? '',
        descriptionVi: json['description_vi']?.toString() ?? '',
        approvedReference: json['approved_reference']?.toString(),
        referenceUrl: json['reference_url']?.toString(),
      );
}

class Shot {
  const Shot({
    required this.shotIndex,
    required this.keyframePrompt,
    this.durationSeconds = 5,
    this.characters = const [],
    this.emotion = '',
    this.shotType = 'medium',
    this.movement = 'static',
    this.angle = 'eye_level',
    this.animationMode = 'image_to_video',
    this.motionStrength = 0.45,
    this.take = 1,
    this.narration = '',
    this.dialogue = const [],
    this.motionPrompt = '',
    this.negativePrompt = '',
    this.version = 1,
    this.keyframeOverride,
  });

  final int shotIndex;
  final String keyframePrompt;
  final double durationSeconds;
  final List<String> characters;
  final String emotion;
  final String shotType;
  final String movement;
  final String angle;
  final String animationMode;
  final double motionStrength;
  final int take;
  final String narration;
  final List<DialogueLine> dialogue;
  final String motionPrompt;
  final String negativePrompt;
  final int version;
  final String? keyframeOverride;

  factory Shot.fromJson(Map<String, dynamic> json) {
    final camera = Map<String, dynamic>.from(json['camera'] as Map? ?? const {});
    final animation = Map<String, dynamic>.from(json['animation'] as Map? ?? const {});
    return Shot(
      shotIndex: _int(json['shot_index'], 1),
      keyframePrompt: json['keyframe_prompt']?.toString() ?? '',
      durationSeconds: _double(json['duration_seconds'], 5),
      characters: _strings(json['characters']),
      emotion: json['emotion']?.toString() ?? '',
      shotType: camera['shot_type']?.toString() ?? 'medium',
      movement: camera['movement']?.toString() ?? 'static',
      angle: camera['angle']?.toString() ?? 'eye_level',
      animationMode: animation['mode']?.toString() ?? 'image_to_video',
      motionStrength: _double(animation['motion_strength'], 0.45),
      take: _int(animation['take'], 1),
      narration: json['narration']?.toString() ?? '',
      dialogue: DialogueLine.listFrom(json['dialogue']),
      motionPrompt: json['motion_prompt']?.toString() ?? '',
      negativePrompt: json['negative_prompt']?.toString() ?? '',
      version: _int(json['version'], 1),
      keyframeOverride: json['keyframe_override']?.toString(),
    );
  }
}

class ComicScene {
  const ComicScene({
    required this.sceneIndex,
    required this.visualPrompt,
    this.narration = '',
    this.dialogue = const [],
    this.characters = const [],
    this.location = '',
    this.locationId = '',
    this.emotion = '',
    this.negativePrompt = '',
    this.cameraMotion = 'slow_zoom_in',
    this.version = 1,
    this.imageOverride,
    this.fixNoteVi = '',
    this.take = 1,
    this.shots = const [],
  });

  final int sceneIndex;
  final String visualPrompt;
  final String narration;
  final List<DialogueLine> dialogue;
  final List<String> characters;
  final String location;
  final String locationId;
  final String emotion;
  final String negativePrompt;
  final String cameraMotion;
  final int version;
  final String? imageOverride;
  final String fixNoteVi;
  final int take;
  final List<Shot> shots;

  factory ComicScene.fromJson(Map<String, dynamic> json) => ComicScene(
        sceneIndex: _int(json['scene_index'], 1),
        visualPrompt: json['visual_prompt']?.toString() ?? '',
        narration: json['narration']?.toString() ?? '',
        dialogue: DialogueLine.listFrom(json['dialogue']),
        characters: _strings(json['characters']),
        location: json['location']?.toString() ?? '',
        locationId: json['location_id']?.toString() ?? '',
        emotion: json['emotion']?.toString() ?? '',
        negativePrompt: json['negative_prompt']?.toString() ?? '',
        cameraMotion: json['camera_motion']?.toString() ?? 'slow_zoom_in',
        version: _int(json['version'], 1),
        imageOverride: json['image_override']?.toString(),
        fixNoteVi: json['fix_note_vi']?.toString() ?? '',
        take: _int(json['take'], 1),
        shots: [
          for (final item in (json['shots'] as List? ?? const []))
            if (item is Map) Shot.fromJson(Map<String, dynamic>.from(item)),
        ],
      );
}

class Storyboard {
  const Storyboard({
    required this.revision,
    required this.mode,
    required this.scenes,
    this.title = '',
    this.analysisMethod = '',
    this.characters = const [],
    this.locations = const [],
    this.world = '',
    this.worldVi = '',
    this.warnings = const [],
  });

  final int revision;
  final String mode;
  final List<ComicScene> scenes;
  final String title;
  final String analysisMethod;
  final List<ComicCharacter> characters;
  final List<ComicLocation> locations;
  final String world;
  final String worldVi;
  final List<String> warnings;

  bool get animated => mode == 'animated';

  ComicCharacter? character(String id) => characters.where((c) => c.id == id).firstOrNull;

  ComicLocation? location(String id) => locations.where((p) => p.id == id).firstOrNull;

  factory Storyboard.fromJson(Map<String, dynamic> json) => Storyboard(
        revision: _int(json['revision'], 1),
        mode: json['mode']?.toString() ?? 'comic',
        title: json['title']?.toString() ?? '',
        analysisMethod: json['analysis_method']?.toString() ?? '',
        world: json['world']?.toString() ?? '',
        worldVi: json['world_vi']?.toString() ?? '',
        warnings: _strings(json['warnings']),
        characters: [
          for (final item in (json['characters'] as List? ?? const []))
            if (item is Map) ComicCharacter.fromJson(Map<String, dynamic>.from(item)),
        ],
        locations: [
          for (final item in (json['locations'] as List? ?? const []))
            if (item is Map) ComicLocation.fromJson(Map<String, dynamic>.from(item)),
        ],
        scenes: [
          for (final item in (json['scenes'] as List? ?? const []))
            if (item is Map) ComicScene.fromJson(Map<String, dynamic>.from(item)),
        ],
      );
}

class ScenesPagination {
  const ScenesPagination({
    this.page = 1,
    this.limit = 6,
    this.totalScenes = 0,
    this.totalPages = 1,
    this.totalShots = 0,
  });

  final int page;
  final int limit;
  final int totalScenes;
  final int totalPages;
  final int totalShots;

  factory ScenesPagination.fromJson(Map<String, dynamic>? json) => json == null
      ? const ScenesPagination()
      : ScenesPagination(
          page: _int(json['page'], 1),
          limit: _int(json['limit'], 6),
          totalScenes: _int(json['total_scenes'], 0),
          totalPages: _int(json['total_pages'], 1),
          totalShots: _int(json['total_shots'], 0),
        );
}

/// Server paths (`/api/v1/comic/projects/{id}/media/...`) of the project's pictures and clips.
class ComicMedia {
  const ComicMedia(this.raw);

  final Map<String, dynamic> raw;

  Map<String, String> _map(String key) => _stringMap(raw[key]);

  Map<String, String> get scenes => _map('scenes');
  Map<String, String> get references => _map('references');
  Map<String, String> get locationReferences => _map('location_references');
  Map<String, String> get locationDrawn => _map('location_drawn');
  Map<String, String> get uploads => _map('uploads');
  Map<String, String> get keyframes => _map('keyframes');
  Map<String, String> get animations => _map('animations');
  Map<String, String> get previews => _map('previews');
  Map<String, String> get keyframeUploads => _map('keyframe_uploads');
  bool get placeReferencesSupported => raw['place_references_supported'] == true;

  /// An uploaded picture wins over the drawn one, as on the web.
  String? sceneImage(int sceneIndex) => uploads['$sceneIndex'] ?? scenes['$sceneIndex'];

  String? shotStill(String label) => keyframeUploads[label] ?? keyframes[label];

  String? shotClip(String label) => previews[label] ?? animations[label];
}

/// `job.comic` (comic_summary on the backend).
class ComicJobInfo {
  const ComicJobInfo(this.raw);

  final Map<String, dynamic> raw;

  factory ComicJobInfo.of(Job job) => ComicJobInfo(Map<String, dynamic>.from(job.raw['comic'] as Map? ?? const {}));

  String get title => raw['title']?.toString() ?? '';
  String get youtubeTitle => raw['youtube_title']?.toString() ?? '';
  String? get seriesId => raw['series_id']?.toString();
  String get seriesTitle => raw['series_title']?.toString() ?? '';
  int? get episodeNumber => (raw['episode_number'] as num?)?.toInt();
  String get videoMode => raw['video_mode']?.toString() ?? 'comic';
  int? get scenes => (raw['scenes'] as num?)?.toInt();
  int? get shots => (raw['shots'] as num?)?.toInt();
  String get aspectRatio => raw['aspect_ratio']?.toString() ?? '';
  double? get duration => (raw['duration'] as num?)?.toDouble();

  /// "Tập 3 · Tên tập" for an episode, the title for a standalone video.
  String episodeTitle() {
    if (seriesId == null) return title;
    final number = 'Tập $episodeNumber';
    return title.isNotEmpty && title != number ? '$number · $title' : number;
  }

  String meta() => [
        videoMode == 'animated' ? 'Hoạt hình' : 'Truyện tranh',
        if (scenes != null && scenes! > 0) '$scenes cảnh',
        if (shots != null && shots! > 0) '$shots shot',
        if (aspectRatio.isNotEmpty) aspectRatio,
      ].join(' · ');
}

/// Comic jobs report progress as a fraction 0..1 (comic_service.py), unlike
/// translation jobs (0..100).
double comicProgress(Job job) {
  final value = job.raw['progress'];
  return value is num ? value.toDouble().clamp(0, 1) : 0;
}

/// `GET /api/v1/comic/projects/{job_id}`.
class ComicProject {
  const ComicProject({
    required this.job,
    required this.media,
    required this.pagination,
    this.settings = const {},
    this.storyboard,
    this.renderedRevision,
    this.shotReports = const {},
    this.metrics,
  });

  final Job job;
  final Map<String, dynamic> settings;
  final Storyboard? storyboard;
  final ScenesPagination pagination;
  final int? renderedRevision;
  final ComicMedia media;
  final Map<String, Map<String, dynamic>> shotReports;
  final Map<String, dynamic>? metrics;

  bool get locked => job.isActive;

  /// The storyboard changed since the last exported video.
  bool get stale => storyboard != null && renderedRevision != storyboard!.revision;

  factory ComicProject.fromJson(Map<String, dynamic> json) => ComicProject(
        job: Job.fromJson(Map<String, dynamic>.from(json['job'] as Map)),
        settings: Map<String, dynamic>.from(json['settings'] as Map? ?? const {}),
        storyboard: json['storyboard'] is Map
            ? Storyboard.fromJson(Map<String, dynamic>.from(json['storyboard'] as Map))
            : null,
        pagination: ScenesPagination.fromJson((json['scenes_pagination'] as Map?)?.cast<String, dynamic>()),
        renderedRevision: (json['rendered_revision'] as num?)?.toInt(),
        media: ComicMedia(Map<String, dynamic>.from(json['media'] as Map? ?? const {})),
        shotReports: {
          for (final entry in (json['shots'] as Map? ?? const {}).entries)
            if (entry.value is Map) entry.key.toString(): Map<String, dynamic>.from(entry.value as Map),
        },
        metrics: (json['metrics'] as Map?)?.cast<String, dynamic>(),
      );
}

class ComicProjectPage {
  const ComicProjectPage({required this.items, required this.page, required this.totalPages, required this.total});

  final List<Job> items;
  final int page;
  final int totalPages;
  final int total;

  bool get hasMore => page < totalPages;

  factory ComicProjectPage.fromJson(Map<String, dynamic> json) => ComicProjectPage(
        items: [
          for (final item in (json['items'] as List? ?? const []))
            if (item is Map) Job.fromJson(Map<String, dynamic>.from(item)),
        ],
        page: _int(json['page'], 1),
        totalPages: _int(json['total_pages'], 1),
        total: _int(json['total'], 0),
      );
}

class ReferenceVariant {
  const ReferenceVariant({
    required this.name,
    required this.url,
    required this.approved,
    required this.uploaded,
    required this.version,
  });

  final String name;
  final String url;
  final bool approved;
  final bool uploaded;
  final int version;

  factory ReferenceVariant.fromJson(Map<String, dynamic> json) => ReferenceVariant(
        name: json['name']?.toString() ?? '',
        url: json['url']?.toString() ?? '',
        approved: json['approved'] == true,
        uploaded: json['uploaded'] == true,
        version: _int(json['version'], 1),
      );
}

class ReferenceList {
  const ReferenceList({this.approved, this.variants = const []});

  final String? approved;
  final List<ReferenceVariant> variants;

  factory ReferenceList.fromJson(Map<String, dynamic> json) => ReferenceList(
        approved: json['approved']?.toString(),
        variants: [
          for (final item in (json['variants'] as List? ?? const []))
            if (item is Map) ReferenceVariant.fromJson(Map<String, dynamic>.from(item)),
        ],
      );
}

class SeriesSummary {
  const SeriesSummary({
    required this.seriesId,
    required this.title,
    this.description = '',
    this.episodes = 0,
    this.characters = 0,
    this.style,
    this.videoMode,
    this.aspectRatio,
    this.updatedAt,
  });

  final String seriesId;
  final String title;
  final String description;
  final int episodes;
  final int characters;
  final String? style;
  final String? videoMode;
  final String? aspectRatio;
  final String? updatedAt;

  factory SeriesSummary.fromJson(Map<String, dynamic> json) => SeriesSummary(
        seriesId: json['series_id']?.toString() ?? '',
        title: json['title']?.toString() ?? '',
        description: json['description']?.toString() ?? '',
        episodes: _int(json['episodes'], 0),
        characters: json['characters'] is num ? _int(json['characters'], 0) : (json['characters'] as List?)?.length ?? 0,
        style: json['style']?.toString(),
        videoMode: json['video_mode']?.toString(),
        aspectRatio: json['aspect_ratio']?.toString(),
        updatedAt: json['updated_at']?.toString(),
      );
}

/// `GET /api/v1/comic/series/{id}`: the shared settings, bible and episodes.
class SeriesDetail {
  const SeriesDetail({
    required this.summary,
    required this.settings,
    required this.characters,
    required this.locations,
    required this.episodes,
    required this.nextEpisodeNumber,
    this.world = '',
    this.worldVi = '',
  });

  final SeriesSummary summary;
  final Map<String, dynamic> settings;
  final List<ComicCharacter> characters;
  final List<ComicLocation> locations;

  /// Episode jobs; `raw['episode_number']` holds the number.
  final List<Job> episodes;
  final int nextEpisodeNumber;
  final String world;
  final String worldVi;

  String get id => summary.seriesId;
  String get storyBrief => settings['story_brief']?.toString() ?? '';
  bool get anyActive => episodes.any((job) => job.isActive);

  factory SeriesDetail.fromJson(Map<String, dynamic> json) {
    final episodes = [
      for (final item in (json['episode_list'] as List? ?? const []))
        if (item is Map) Job.fromJson(Map<String, dynamic>.from(item)),
    ]..sort((a, b) => _int(a.raw['episode_number'], 0).compareTo(_int(b.raw['episode_number'], 0)));
    return SeriesDetail(
      summary: SeriesSummary.fromJson(json),
      settings: Map<String, dynamic>.from(json['settings'] as Map? ?? const {}),
      world: json['world']?.toString() ?? '',
      worldVi: json['world_vi']?.toString() ?? '',
      characters: [
        for (final item in (json['characters'] as List? ?? const []))
          if (item is Map) ComicCharacter.fromJson(Map<String, dynamic>.from(item)),
      ],
      locations: [
        for (final item in (json['locations'] as List? ?? const []))
          if (item is Map) ComicLocation.fromJson(Map<String, dynamic>.from(item)),
      ],
      episodes: episodes,
      nextEpisodeNumber: _int(json['next_episode_number'], episodes.length + 1),
    );
  }
}
