"""Animated-mode storyboard: scenes made of shots (one camera set-up and one motion each).

Short shots with one simple action are what current image-to-video models
handle well, so the prompt asks for 3-8 second shots and the rule-based
fallback cuts sentences into shots of that length.
"""
from __future__ import annotations

from typing import Any

from domain.comic import (
    DEFAULT_NEGATIVE_PROMPT,
    SHOT_ANGLES,
    SHOT_MOVEMENTS,
    SHOT_TYPES,
    ComicScene,
    ComicSettings,
    Shot,
)
from domain.languages import language_profile
from infrastructure.story_text import split_sentences

MAX_SHOT_SECONDS = 8.0
_HEURISTIC_CAMERA = (
    ("wide", "push_in"), ("medium", "tracking"), ("close_up", "static"), ("medium", "pan_right"),
    ("wide", "pull_out"), ("close_up", "push_in"),
)
DEFAULT_MOTION_PROMPT = "subtle natural character motion, hair and clothes move gently, smooth camera"

_DIALOGUE = {"type": "array", "items": {"type": "object", "properties": {
    "character": {"type": "string"}, "text": {"type": "string"}}, "required": ["character", "text"]}}
ANIMATED_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "characters": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "name": {"type": "string"},
            "gender": {"type": "string", "enum": ["female", "male", "unknown"]},
            "age": {"type": "string"}, "appearance": {"type": "string"}, "outfit": {"type": "string"},
        }, "required": ["id", "name", "gender", "appearance"]}},
        "scenes": {"type": "array", "items": {"type": "object", "properties": {
            "location": {"type": "string"}, "emotion": {"type": "string"},
            "visual_prompt": {"type": "string"}, "source_excerpt": {"type": "string"},
            "characters": {"type": "array", "items": {"type": "string"}},
            "shots": {"type": "array", "items": {"type": "object", "properties": {
                "duration_seconds": {"type": "number"},
                "characters": {"type": "array", "items": {"type": "string"}},
                "action": {"type": "string"}, "emotion": {"type": "string"},
                "camera": {"type": "object", "properties": {
                    "shot_type": {"type": "string", "enum": list(SHOT_TYPES)},
                    "movement": {"type": "string", "enum": list(SHOT_MOVEMENTS)},
                    "angle": {"type": "string", "enum": list(SHOT_ANGLES)},
                }},
                "narration": {"type": "string"}, "dialogue": _DIALOGUE,
                "keyframe_prompt": {"type": "string"}, "motion_prompt": {"type": "string"},
            }, "required": ["characters", "action", "camera", "narration", "dialogue", "keyframe_prompt",
                            "motion_prompt"]}},
        }, "required": ["visual_prompt", "shots"]}},
    },
    "required": ["characters", "scenes"],
}


def animated_system_prompt(settings: ComicSettings, chars_per_second: float) -> str:
    language = language_profile(settings.language).label_vi
    max_chars = int(MAX_SHOT_SECONDS * chars_per_second)
    return f"""You are an animation director turning prose into a short 2D animated film.
Answer with ONE JSON object only, no markdown:
{{"characters": [{{"id", "name", "gender": "female|male|unknown", "age", "appearance", "outfit"}}],
 "scenes": [{{"location", "emotion", "visual_prompt", "source_excerpt", "characters": ["<id>"],
   "shots": [{{"duration_seconds", "characters": ["<id>"], "action", "emotion",
     "camera": {{"shot_type", "movement", "angle"}}, "narration", "dialogue": [{{"character": "<id>", "text"}}],
     "keyframe_prompt", "motion_prompt"}}]}}]}}
Rules:
1. Cover the story chunk in order. Never skip, reorder or invent events, names or outcomes.
2. A scene is one situation in one place; split it into 1-4 shots. A shot is ONE camera set-up with ONE
   simple, physically plausible action (walking, turning the head, a gust of wind) lasting 3-8 seconds.
   Prefer several short shots to one complex shot: video models fail on long or compound actions.
3. Separate narration from dialogue, in {language}. "narration" keeps the author's wording; spoken lines go to
   "dialogue" attributed to the speaker's character id. Keep each shot's narration + dialogue under {max_chars}
   characters so it can be spoken within the shot.
4. "visual_prompt" (scene) is an ENGLISH description of the environment shared by its shots.
5. "keyframe_prompt" is ENGLISH and describes the first frame: who (name plus a short look), pose,
   setting, lighting and framing. No text or speech bubbles.
6. "motion_prompt" is ENGLISH and describes only what moves during the shot and how the camera moves,
   e.g. "Minh walks slowly toward the camera, leaves drift in the wind, slow tracking shot".
7. camera.shot_type is one of {", ".join(SHOT_TYPES)}; camera.movement one of {", ".join(SHOT_MOVEMENTS)};
   camera.angle one of {", ".join(SHOT_ANGLES)}. Open a new place with an establishing or wide shot.
8. Characters: reuse ids from KNOWN CHARACTERS exactly. Add a character only for a named or recurring person,
   with a lowercase ascii id and a fixed ENGLISH "appearance" (age look, hair, eyes, build, palette,
   distinctive accessories) so it is drawn identically in every shot.
9. "source_excerpt" copies verbatim the first 8-15 words of the source passage the scene covers.
10. "emotion" is one English word; "location" is a short English place description."""


def prepare_animated_draft(data: Any) -> Any:
    """Fill indexes and scene fields the model is not asked for."""
    if isinstance(data, dict) and isinstance(data.get("scenes"), list):
        for position, scene in enumerate(data["scenes"], 1):
            if not isinstance(scene, dict):
                continue
            scene.setdefault("scene_index", position)
            shots = scene.get("shots") if isinstance(scene.get("shots"), list) else []
            for shot_position, shot in enumerate(shots, 1):
                if isinstance(shot, dict):
                    shot.setdefault("shot_index", shot_position)
                    shot.setdefault("keyframe_prompt", shot.get("action") or scene.get("visual_prompt") or "")
            if not scene.get("characters"):
                scene["characters"] = list(dict.fromkeys(
                    cid for shot in shots if isinstance(shot, dict) for cid in shot.get("characters") or []))
            if shots and not scene.get("narration"):
                # The scene card shows a summary; speech itself lives on the shots.
                scene["narration"] = " ".join(str(s.get("narration") or "") for s in shots if isinstance(s, dict))[:1500]
    return data


def heuristic_animated_scenes(chunk: str, settings: ComicSettings, start_index: int,
                              chars_per_second: float) -> list[ComicScene]:
    """One scene per paragraph, one shot per sentence group that fits in a shot."""
    budget = MAX_SHOT_SECONDS * chars_per_second * 0.8
    scenes: list[ComicScene] = []
    for paragraph in [p for p in chunk.split("\n\n") if p.strip()]:
        groups: list[list[str]] = []
        for sentence in split_sentences(paragraph):
            if groups and sum(len(s) for s in groups[-1]) + len(sentence) <= budget and len(groups[-1]) < 3:
                groups[-1].append(sentence)
            else:
                groups.append([sentence])
        shots = []
        for offset, group in enumerate(groups[:8]):
            narration = " ".join(group)[:800]
            shot_type, movement = _HEURISTIC_CAMERA[(len(scenes) + offset) % len(_HEURISTIC_CAMERA)]
            shots.append(Shot(
                shot_index=offset + 1, narration=narration,
                duration_seconds=max(3.0, min(MAX_SHOT_SECONDS, len(narration) / chars_per_second + 0.5)),
                camera={"shot_type": shot_type, "movement": movement},
                animation={"motion_strength": settings.motion_strength},
                keyframe_prompt=f"Animation keyframe of this story moment: {narration[:500]}",
                motion_prompt=DEFAULT_MOTION_PROMPT, negative_prompt=DEFAULT_NEGATIVE_PROMPT,
            ))
        if not shots:
            continue
        summary = " ".join(shot.narration for shot in shots)[:1500]
        scenes.append(ComicScene(
            scene_index=start_index + len(scenes), narration=summary, shots=shots,
            visual_prompt=f"Setting of this story moment: {summary[:600]}", source_excerpt=summary[:120],
        ))
    return scenes


def finalize_shots(scene: ComicScene, settings: ComicSettings) -> None:
    for shot in scene.shots:
        if not shot.negative_prompt:
            shot.negative_prompt = DEFAULT_NEGATIVE_PROMPT
        if not shot.motion_prompt:
            shot.motion_prompt = DEFAULT_MOTION_PROMPT
        if "motion_strength" not in shot.animation.model_fields_set:
            shot.animation.motion_strength = settings.motion_strength
