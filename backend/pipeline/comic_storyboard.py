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
        "characters": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "name": {"type": "string"},
            "gender": {"type": "string", "enum": ["female", "male", "unknown"]},
            "age": {"type": "string"}, "appearance": {"type": "string"}, "outfit": {"type": "string"},
        }, "required": ["id", "name", "gender", "appearance"]}},
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
{{"characters": [{{"id", "name", "gender": "female|male|unknown", "age", "appearance", "outfit"}}],
 "scenes": [{{"narration", "dialogue": [{{"character": "<character id>", "text"}}], "characters": ["<id>"],
   "location", "emotion", "visual_prompt", "negative_prompt", "camera_motion", "estimated_duration", "source_excerpt"}}]}}
Rules:
1. Cover the story chunk in order. Never skip, reorder or invent events, names or outcomes.
2. Separate narration from dialogue. "narration" keeps the author's wording in {language} (you may drop filler);
   spoken lines go to "dialogue" in {language}, attributed to the speaker's character id. Use "narrator" never.
3. One scene = one visual moment with a single main action. Keep narration + dialogue of a scene under
   {max_chars} characters (about {settings.target_scene_duration:g} seconds spoken). estimated_duration is in seconds.
4. "visual_prompt" is ENGLISH and describes only what is visible: who (name plus a short look), action, pose,
   setting, time of day, lighting, shot type (close-up, medium, wide). No text, captions or speech bubbles.
5. Characters: reuse ids from KNOWN CHARACTERS exactly. Add a character only for a named or recurring person,
   with a lowercase ascii id. "appearance" is ENGLISH and lists fixed traits (age look, hair, eyes, build,
   distinctive accessories) so the character can be drawn the same way in every scene.
6. "camera_motion" is one of: {", ".join(CAMERA_MOTIONS)}. Vary it; use slow_zoom_in for tension or emotion.
7. "source_excerpt" copies verbatim the first 8-15 words of the source passage the scene covers.
8. "emotion" is one English word; "location" is a short English place description."""


def user_prompt(chunk: str, characters: list[ComicCharacter], position: int, total: int) -> str:
    known = [c.model_dump(include={"id", "name", "gender", "appearance", "outfit"}) for c in characters]
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


def _validate_draft(answer: str, animated: bool) -> StoryboardDraft:
    data = extract_json(answer)
    draft = StoryboardDraft.model_validate(prepare_animated_draft(data) if animated else _prepare_draft(data))
    if animated:
        missing = [scene.scene_index for scene in draft.scenes if not scene.shots]
        if missing:
            raise ValueError(f"scenes {missing} have no shots; every scene needs 1-4 shots")
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
            return _validate_draft(answer, animated)
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
) -> Storyboard:
    if not text.strip():
        raise ValueError("Truyện không có nội dung")
    chunks = chunk_story(text, CHUNK_CHARS)
    registry: dict[str, ComicCharacter] = {}
    scenes: list[ComicScene] = []
    warnings: list[str] = []
    methods: set[str] = set()
    for position, chunk in enumerate(chunks, 1):
        check_active()
        draft_scenes: list[ComicScene] | None = None
        if llm is not None:
            try:
                draft = analyze_chunk(llm, settings, chunk, list(registry.values()), position, len(chunks))
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
        scenes.extend(draft_scenes)
        progress(position, len(chunks))

    if len(scenes) > settings.max_scenes:
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
        language=settings.language,
        style=settings.style,
        mode=settings.video_mode,
        analysis_method="+".join(sorted(methods)),
        characters=characters,
        scenes=scenes,
        warnings=warnings,
    )
