import 'comic_options.dart';

/// The "Tạo truyện mới" form (#comicForm in web/index.html), sent to
/// `POST /api/v1/comic/series`. Defaults match the web form.
class StoryForm {
  StoryForm({
    this.title = 'Truyện mới',
    this.genre = '',
    this.text = '',
    this.videoMode = 'comic',
    this.aspectRatio = '9:16',
    this.description = '',
    this.storyBrief = '',
    this.episodeTitle = '',
    this.youtubeTitle = '',
    this.animationProvider = 'camera_2_5d',
    this.quality = 'final',
    this.fps = 24,
    this.motionStrength = 0.45,
    this.maxAnimationAttempts = 2,
    this.animationBudgetSeconds = 240,
    this.style = 'manhua',
    this.llmProvider = 'auto',
    this.imageProvider = 'pollinations',
    this.language = 'vi',
    this.ttsProvider = 'edge',
    this.narratorVoice = 'female',
    this.citNarratorVoice = '',
    this.citFemaleVoice = '',
    this.citMaleVoice = '',
    this.speechRate = 1,
    this.targetSceneDuration = 7,
    this.seed = 0,
    this.musicVolume = 0.15,
    this.burnSubtitles = true,
    this.autoRender = true,
    this.applyLogo = true,
  });

  /// Defaults that depend on what the server has configured (loadOptions on the web).
  factory StoryForm.withOptions(ComicOptions options) {
    final form = StoryForm(imageProvider: options.defaultImageProvider, ttsProvider: options.defaultTtsProvider);
    form.syncCitVoices(options);
    return form;
  }

  String title;
  String genre;
  String text;
  String videoMode;
  String aspectRatio;
  String description;
  String storyBrief;
  String episodeTitle;
  String youtubeTitle;
  String animationProvider;
  String quality;
  int fps;
  double motionStrength;
  int maxAnimationAttempts;
  double animationBudgetSeconds;
  String style;
  String llmProvider;
  String imageProvider;
  String language;
  String ttsProvider;
  String narratorVoice;
  String citNarratorVoice;
  String citFemaleVoice;
  String citMaleVoice;
  double speechRate;
  double targetSceneDuration;
  int seed;
  double musicVolume;
  bool burnSubtitles;
  bool autoRender;

  /// Stamp the account logo on the video; sent as true only when [toFields]
  /// is told the account has a logo (the server refuses it otherwise).
  bool applyLogo;

  bool get animated => videoMode == 'animated';
  bool get usesCit => ttsProvider == 'cit';

  /// Picking a genre also picks its drawing style when that style exists.
  void selectGenre(GenrePreset? preset, ComicOptions options) {
    genre = preset == null || genre == preset.key ? '' : preset.key;
    if (genre.isNotEmpty && options.styles.any((s) => s.key == preset!.style)) style = preset!.style;
  }

  /// Keeps each CIT voice valid for the current narrator gender (syncTtsVoices).
  void syncCitVoices(ComicOptions options) {
    final cit = options.tts('cit');
    if (!usesCit || cit == null) return;
    String pick(String current, String gender) {
      final voices = cit.voicesFor(gender);
      if (voices.any((voice) => voice.key == current)) return current;
      return voices.isEmpty ? '' : voices.first.key;
    }

    citNarratorVoice = pick(citNarratorVoice, narratorVoice);
    citFemaleVoice = pick(citFemaleVoice, 'female');
    citMaleVoice = pick(citMaleVoice, 'male');
  }

  /// The multipart fields, exactly as the web form posts them. [bible] is the
  /// reviewed bible JSON, omitted to let the AI decide.
  Map<String, String> toFields({String? bible, bool hasLogo = false}) => {
        'title': title,
        'genre': genre,
        'text': text,
        'video_mode': videoMode,
        'aspect_ratio': aspectRatio,
        'description': description,
        'story_brief': storyBrief,
        'episode_title': episodeTitle,
        'youtube_title': youtubeTitle,
        'animation_provider': animationProvider,
        'quality': quality,
        'fps': '$fps',
        'motion_strength': _num(motionStrength),
        'max_animation_attempts': '$maxAnimationAttempts',
        'animation_budget_seconds': _num(animationBudgetSeconds),
        'style': style,
        'llm_provider': llmProvider,
        'image_provider': imageProvider,
        'language': language,
        'tts_provider': ttsProvider,
        'narrator_voice': narratorVoice,
        // Hidden on the web unless CIT is chosen; an empty select posts "".
        'cit_narrator_voice': usesCit ? citNarratorVoice : '',
        'cit_female_voice': usesCit ? citFemaleVoice : '',
        'cit_male_voice': usesCit ? citMaleVoice : '',
        'speech_rate': _num(speechRate),
        'target_scene_duration': _num(targetSceneDuration),
        'seed': '$seed',
        'music_volume': _num(musicVolume),
        'burn_subtitles': burnSubtitles ? 'true' : 'false',
        'auto_render': autoRender ? 'true' : 'false',
        'apply_logo': applyLogo && hasLogo ? 'true' : 'false',
        'bible': ?bible,
      };

  /// Fields for `POST /comic/bible/suggest` from the story step.
  Map<String, String> suggestFields() => {
        'text': text,
        'story_brief': storyBrief,
        'genre': genre,
        'language': language,
        'llm_provider': llmProvider,
      };
}

/// "Thêm tập N" (#comicEpisodeForm), sent to `POST /api/v1/comic/projects`
/// with `series_id`; the series' settings apply.
class EpisodeForm {
  EpisodeForm({
    required this.seriesId,
    this.title = '',
    this.youtubeTitle = '',
    this.text = '',
    this.episodeBrief = '',
    this.autoRender = true,
    this.applyLogo = true,
  });

  final String seriesId;
  String title;
  String youtubeTitle;
  String text;
  String episodeBrief;
  bool autoRender;
  bool applyLogo;

  /// [bible]: only the new characters and places the user approved; omitted
  /// when there is nothing new.
  Map<String, String> toFields({String? bible, bool hasLogo = false}) => {
        'title': title,
        'youtube_title': youtubeTitle,
        'text': text,
        'episode_brief': episodeBrief,
        'auto_render': autoRender ? 'true' : 'false',
        'apply_logo': applyLogo && hasLogo ? 'true' : 'false',
        'series_id': seriesId,
        'bible': ?bible,
      };

  /// The episode's story is compared with the series' bible.
  Map<String, String> suggestFields({required String language, required String llmProvider}) => {
        'text': text,
        'story_brief': episodeBrief,
        'series_id': seriesId,
        'language': language,
        'llm_provider': llmProvider,
      };
}

/// "1" rather than "1.0", like an HTML number input.
String _num(double value) => value == value.roundToDouble() ? value.toInt().toString() : value.toString();
