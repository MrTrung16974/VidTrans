"""Story text -> validated storyboard (characters + scenes).

The LLM must answer with JSON that passes the Pydantic contracts in
``domain.comic``; invalid answers get a bounded number of repair rounds that
quote the validation errors. Without an LLM (or when it keeps failing in
``auto`` mode) a rule-based splitter groups sentences into scenes, so a job
always produces an editable storyboard.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any, Callable

from pydantic import ValidationError

from domain.comic import (
    CAMERA_MOTIONS,
    DEFAULT_NEGATIVE_PROMPT,
    ComicCharacter,
    ComicScene,
    ComicSettings,
    Storyboard,
    StoryboardDraft,
)
from domain.languages import language_profile
from infrastructure.story_text import chunk_story, split_sentences
from pipeline.animation_storyboard import (
    ANIMATED_DRAFT_SCHEMA,
    animated_system_prompt,
    finalize_shots,
    heuristic_animated_scenes,
    prepare_animated_draft,
)

logger = logging.getLogger(__name__)

MAX_REPAIR_ROUNDS = 2
CHUNK_CHARS = 3500
# Spoken characters per second for pacing scenes; Vietnamese TTS is ~14-15 chars/s.
CHARS_PER_SECOND = 14.0
_HEURISTIC_MOTIONS = ("slow_zoom_in", "pan_right", "slow_zoom_out", "pan_left", "pan_up")

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "youtube_title": {"type": "string"},
        "characters": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "name": {"type": "string"},
            "gender": {"type": "string", "enum": ["female", "male", "unknown"]},
            "species": {"type": "string"},
            "age": {"type": "string"}, "appearance": {"type": "string"}, "outfit": {"type": "string"},
        }, "required": ["id", "name", "gender", "species", "appearance"]}},
        "scenes": {"type": "array", "items": {"type": "object", "properties": {
            "narration": {"type": "string"},
            "dialogue": {"type": "array", "items": {"type": "object", "properties": {
                "character": {"type": "string"}, "text": {"type": "string"}}, "required": ["character", "text"]}},
            "characters": {"type": "array", "items": {"type": "string"}},
            "location": {"type": "string"}, "emotion": {"type": "string"},
            "visual_prompt": {"type": "string"}, "negative_prompt": {"type": "string"},
            "camera_motion": {"type": "string", "enum": list(CAMERA_MOTIONS)},
            "estimated_duration": {"type": "number"}, "source_excerpt": {"type": "string"},
        }, "required": ["narration", "dialogue", "characters", "visual_prompt", "camera_motion"]}},
    },
    "required": ["characters", "scenes"],
}


def system_prompt(settings: ComicSettings) -> str:
    language = language_profile(settings.language).label_vi
    max_chars = int(settings.target_scene_duration * CHARS_PER_SECOND * 1.3)
    return f"""You are a storyboard director turning prose into a narrated comic video.
Answer with ONE JSON object only, no markdown:
{{"youtube_title": "Vietnamese YouTube video title", "characters": [{{"id", "name", "gender": "female|male|unknown", "species", "age", "appearance", "outfit"}}],
 "scenes": [{{"narration", "dialogue": [{{"character": "<character id>", "text"}}], "characters": ["<id>"],
   "location", "emotion", "visual_prompt", "negative_prompt", "camera_motion", "estimated_duration", "source_excerpt"}}]}}
Rules:
0. "youtube_title" is a natural, attractive Vietnamese title summarizing this story for YouTube. Keep it under 100
   characters, preserve character names, use no hashtags and no clickbait claims. Return it for the first chunk;
   later chunks may return an empty string.
1. Cover the story chunk in order. Never skip, reorder or invent events, names or outcomes.
2. Separate narration from dialogue. "narration" keeps the author's wording in {language} (you may drop filler);
   spoken lines go to "dialogue" in {language}, attributed to the speaker's character id. Use "narrator" never.
3. One scene = one visual moment with a single main action. Keep narration + dialogue of a scene under
   {max_chars} characters (about {settings.target_scene_duration:g} seconds spoken). estimated_duration is in seconds.
4. "visual_prompt" is ENGLISH, at most 60 words, and describes a cinematic SCENE (a film still), never a
   portrait, in this order: shot type and the place ("Wide shot of a moonlit bamboo courtyard"), the ONE
   action happening now and who does it, where each visible character stands in that place (left / right /
   foreground / background, facing whom), the story props involved (named concretely), environment details
   in foreground, midground and background, time of day, lighting and atmosphere. Prefer wide or medium-wide
   shots that show the surroundings; use a close-up only when the text centres on a face or an object.
   Draw only what the text states. Never summarise or skip sentences, and never repeat a spoken line inside
   "narration". No text, captions or speech bubbles.
5. Characters: reuse ids from KNOWN CHARACTERS exactly. Add a character only for a named or recurring
   person, animal or creature, with a lowercase ascii id. "appearance" is ENGLISH and lists fixed traits
   (age look, hair or fur, eyes, build, distinctive accessories) so it can be drawn the same way in every scene.
   "gender" must follow the story: a male character (anh, hắn, ông, cậu, chàng, he) is "male", a female
   one (cô, nàng, bà, chị, she) is "female". "species" is "human" or the creature in English ("black cat",
   "white fox spirit", "dragon", "robot"); never turn an animal, spirit or monster into a person.
   "age" is a number of years or a life stage ("8", "teenager", "elderly").
6. "camera_motion" is one of: {", ".join(CAMERA_MOTIONS)}. Vary it; use slow_zoom_in for tension or emotion.
7. "source_excerpt" copies verbatim the first 8-15 words of the source passage the scene covers.
8. "emotion" is one English word; "location" is a short English place description."""


def user_prompt(chunk: str, characters: list[ComicCharacter], position: int, total: int) -> str:
    known = [c.model_dump(include={"id", "name", "gender", "species", "age", "appearance", "outfit"})
             for c in characters]
    return (f"KNOWN CHARACTERS: {json.dumps(known, ensure_ascii=False)}\n\n"
            f"STORY CHUNK {position}/{total}:\n{chunk}")


def extract_json(text: str) -> Any:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise json.JSONDecodeError("no JSON object", text, 0)
    return json.loads(text[start:end + 1])


def _prepare_draft(data: Any) -> Any:
    """Fill indexes the model is not asked for; leave real content errors to validation."""
    if isinstance(data, dict) and isinstance(data.get("scenes"), list):
        for position, scene in enumerate(data["scenes"], 1):
            if isinstance(scene, dict):
                scene.setdefault("scene_index", position)
    return data


# Below this a draft silently drops most of the story (a small local model summarising a
# 3500-character chunk into five shots): ask for a repair, then fall back to the sentence splitter.
MIN_COVERAGE = 0.6
MIN_COVERAGE_CHUNK_CHARS = 600


def draft_coverage(draft: StoryboardDraft, chunk: str) -> float:
    """Share of the chunk's text that the draft will actually narrate or speak."""
    spoken = 0
    for scene in draft.scenes:
        for part in scene.shots or [scene]:
            spoken += len(_comparable(" ".join([part.narration, *(line.text for line in part.dialogue)])))
    return spoken / max(1, len(_comparable(chunk)))


def _validate_draft(answer: str, animated: bool, chunk: str = "") -> StoryboardDraft:
    data = extract_json(answer)
    draft = StoryboardDraft.model_validate(prepare_animated_draft(data) if animated else _prepare_draft(data))
    if animated:
        missing = [scene.scene_index for scene in draft.scenes if not scene.shots]
        if missing:
            raise ValueError(f"scenes {missing} have no shots; every scene needs 1-4 shots")
    if len(chunk) >= MIN_COVERAGE_CHUNK_CHARS:
        coverage = draft_coverage(draft, chunk)
        if coverage < MIN_COVERAGE:
            raise ValueError(
                f"the narration and dialogue cover only {coverage:.0%} of the story chunk; keep the author's "
                "wording for EVERY sentence in order and add more scenes/shots instead of summarising")
    return draft


def analyze_chunk(llm, settings: ComicSettings, chunk: str, characters: list[ComicCharacter],
                  position: int, total: int) -> StoryboardDraft:
    animated = settings.video_mode == "animated"
    system = animated_system_prompt(settings, CHARS_PER_SECOND) if animated else system_prompt(settings)
    schema = ANIMATED_DRAFT_SCHEMA if animated else DRAFT_SCHEMA
    prompt = user_prompt(chunk, characters, position, total)
    request = prompt
    for round_number in range(MAX_REPAIR_ROUNDS + 1):
        answer = llm.generate_json(system, request, schema)
        try:
            return _validate_draft(answer, animated, chunk)
        except (ValidationError, ValueError) as exc:
            if round_number == MAX_REPAIR_ROUNDS:
                raise
            problems = str(exc)[:1500]
            logger.info("Storyboard chunk %d/%d invalid (round %d); asking for a repair",
                        position, total, round_number + 1)
            request = (f"{prompt}\n\nYour previous answer was rejected by the validator:\n{problems}\n"
                       "Return the complete corrected JSON object only.")
    raise AssertionError("unreachable")


def heuristic_scenes(chunk: str, settings: ComicSettings, start_index: int = 1) -> list[ComicScene]:
    budget = settings.target_scene_duration * CHARS_PER_SECOND
    groups: list[list[str]] = []
    for sentence in split_sentences(chunk):
        if groups and sum(len(s) for s in groups[-1]) + len(sentence) <= budget:
            groups[-1].append(sentence)
        else:
            groups.append([sentence])
    scenes = []
    for offset, group in enumerate(groups):
        narration = " ".join(group)[:1500]
        scenes.append(ComicScene(
            scene_index=start_index + offset,
            narration=narration,
            visual_prompt=f"Illustration of this story moment: {narration[:600]}",
            camera_motion=_HEURISTIC_MOTIONS[(start_index + offset) % len(_HEURISTIC_MOTIONS)],
            estimated_duration=max(2.0, min(40.0, len(narration) / CHARS_PER_SECOND)),
            source_excerpt=narration[:120],
        ))
    return scenes


def attach_story_context(scenes: list[ComicScene], characters: list[ComicCharacter],
                         previous: list[ComicScene]) -> None:
    """Give sentence-split scenes the cast and place the LLM found in earlier chunks.

    Without them every keyframe draws a different stranger in a different room, and the
    character references (IP-Adapter) are never applied.
    """
    names = [(c.id, f" {_comparable(c.name)} ") for c in characters if len(_comparable(c.name)) >= 2]
    location = next((s.location for s in reversed(previous) if s.location), "")
    cast: list[str] = next((list(_scene_character_ids(s)) for s in reversed(previous) if _scene_character_ids(s)), [])
    for scene in scenes:
        for target in scene.shots or [scene]:
            text = f" {_comparable(target.narration)} "
            found = sorted((text.find(name), cid) for cid, name in names if name in text)
            # A line without a name ("hắn nói…") continues the current conversation.
            if found:
                cast = [cid for _position, cid in found][:3]
            if cast and not target.characters:
                target.characters = list(cast)
        if not scene.characters:
            scene.characters = list(dict.fromkeys(
                cid for shot in scene.shots for cid in shot.characters)) or list(cast)
        if scene.location:
            location = scene.location
        elif location:
            scene.location = location


def translate_heuristic_visuals(scenes: list[ComicScene], translator) -> bool:
    """Convert fallback narration into English prompts for the English-only image model."""
    if translator is None or not scenes:
        return False
    # Animated keyframes are drawn from each shot's prompt, so shots need English too.
    targets: list[tuple[object, str]] = []
    for scene in scenes:
        targets.append((scene, "visual_prompt"))
        targets.extend((shot, "keyframe_prompt") for shot in scene.shots if shot.narration.strip())
    sources = [target.narration.replace("\n", " ").strip()[:1400] for target, _field in targets]
    try:
        translated = translator.translate_lines(sources)
        if len(translated) != len(sources) or not all(str(value).strip() for value in translated):
            return False
    except Exception as exc:
        logger.warning("Could not translate local visual prompts (%s: %s)", type(exc).__name__, str(exc)[:160])
        return False
    for (target, field_name), english in zip(targets, translated):
        setattr(target, field_name, str(english).strip())
    return True


def _comparable(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    return re.sub(r"[\W_]+", " ", text).strip()


def _merge_characters(registry: dict[str, ComicCharacter], draft: StoryboardDraft) -> None:
    for character in draft.characters:
        # The first description wins: later chunks must not redraw a known character.
        registry.setdefault(character.id, character)
    for scene in draft.scenes:
        for cid in _scene_character_ids(scene):
            registry.setdefault(cid, ComicCharacter(id=cid, name=cid.replace("_", " ").title()))


def _scene_character_ids(scene: ComicScene) -> list[str]:
    ids = [*scene.characters, *(line.character for line in scene.dialogue)]
    for shot in scene.shots:
        ids += [*shot.characters, *(line.character for line in shot.dialogue)]
    return ids


def build_storyboard(
    text: str,
    settings: ComicSettings,
    llm,
    *,
    progress: Callable[[int, int], None] = lambda done, total: None,
    check_active: Callable[[], None] = lambda: None,
    known_characters: list[ComicCharacter] = (),
    prompt_translator=None,
) -> Storyboard:
    """``known_characters`` (a series' character bible) seed the registry, so the LLM reuses their
    ids and looks and an episode never redraws a character introduced earlier in the story."""
    if not text.strip():
        raise ValueError("Truyện không có nội dung")
    # Local models lose detail on long chunks; they get smaller ones.
    chunks = chunk_story(text, getattr(llm, "story_chunk_chars", CHUNK_CHARS))
    registry: dict[str, ComicCharacter] = {c.id: c.model_copy() for c in known_characters}
    scenes: list[ComicScene] = []
    warnings: list[str] = []
    methods: set[str] = set()
    generated_youtube_title = ""
    for position, chunk in enumerate(chunks, 1):
        check_active()
        draft_scenes: list[ComicScene] | None = None
        if llm is not None:
            try:
                draft = analyze_chunk(llm, settings, chunk, list(registry.values()), position, len(chunks))
                if not generated_youtube_title and draft.youtube_title.strip():
                    generated_youtube_title = draft.youtube_title.strip()[:100]
                _merge_characters(registry, draft)
                draft_scenes = draft.scenes
                methods.add(llm.name)
            except Exception as exc:
                if settings.llm_provider != "auto":
                    raise
                reason = exc.args[0] if exc.args and isinstance(exc.args[0], str) else type(exc).__name__
                logger.warning("Storyboard LLM failed on chunk %d (%s: %s); using sentence splitter",
                               position, type(exc).__name__, str(reason)[:200])
                warnings.append(f"Đoạn {position}: AI chia cảnh lỗi ({str(reason)[:120]}), đã chia theo câu.")
        if draft_scenes is None:
            if settings.video_mode == "animated":
                draft_scenes = heuristic_animated_scenes(chunk, settings, len(scenes) + 1, CHARS_PER_SECOND)
            else:
                draft_scenes = heuristic_scenes(chunk, settings, len(scenes) + 1)
            methods.add("heuristic")
            attach_story_context(draft_scenes, list(registry.values()), scenes)
            if translate_heuristic_visuals(draft_scenes, prompt_translator):
                methods.add("visual-en-local")
        scenes.extend(draft_scenes)
        progress(position, len(chunks))

    if settings.max_scenes is not None and len(scenes) > settings.max_scenes:
        warnings.append(f"Truyện dài {len(scenes)} cảnh; chỉ giữ {settings.max_scenes} cảnh đầu theo giới hạn.")
        scenes = scenes[: settings.max_scenes]

    source = _comparable(text)
    for position, scene in enumerate(scenes, 1):
        scene.scene_index = position
        if not scene.negative_prompt:
            scene.negative_prompt = DEFAULT_NEGATIVE_PROMPT
        finalize_shots(scene, settings)
        excerpt = _comparable(scene.source_excerpt)
        if excerpt and excerpt[:60] not in source:
            warnings.append(f"Cảnh {position}: trích dẫn không khớp văn bản gốc, nên kiểm tra lại nội dung.")

    used = {cid for scene in scenes for cid in _scene_character_ids(scene)}
    characters = [c for c in registry.values() if c.id in used]
    return Storyboard(
        title=settings.title,
        youtube_title=(settings.youtube_title.strip() or generated_youtube_title or settings.title)[:100],
        language=settings.language,
        style=settings.style,
        mode=settings.video_mode,
        analysis_method="+".join(sorted(methods)),
        characters=characters,
        scenes=scenes,
        warnings=warnings,
    )
