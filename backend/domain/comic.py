"""Contracts for the text-story to comic / animated video feature.

The storyboard is the single editable source of truth: every image, voice clip
and scene clip is derived from it and cached by a hash of the fields it uses,
so editing one scene only regenerates that scene.

``comic`` mode renders one still image per scene with pan/zoom. ``animated``
mode splits every scene into shots (one camera set-up each); every shot gets a
keyframe that an image-to-video model animates.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.languages import language_profile

STORYBOARD_SCHEMA_VERSION = 1

CameraMotion = Literal["slow_zoom_in", "slow_zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"]
CAMERA_MOTIONS: tuple[str, ...] = CameraMotion.__args__  # type: ignore[attr-defined]

ASPECT_RATIOS: dict[str, tuple[int, int]] = {"9:16": (1080, 1920), "16:9": (1920, 1080)}

# Prompt prefixes are English: image models follow English style keywords far more reliably.
COMIC_STYLES: dict[str, tuple[str, str]] = {
    "anime_comic": ("Anime / truyện tranh Nhật", "anime comic illustration, clean line art, cel shading, vibrant colors"),
    "manhwa": ("Manhwa / webtoon Hàn", "korean webtoon manhwa illustration, soft cel shading, detailed eyes, cinematic lighting"),
    "manhua": ("Manhua cổ trang", "chinese manhua illustration, wuxia fantasy, flowing robes, painterly lighting"),
    "western_comic": ("Comic Âu Mỹ", "western comic book illustration, bold ink outlines, halftone shading, dramatic colors"),
    "storybook": ("Tranh truyện thiếu nhi", "children's storybook watercolor illustration, soft pastel palette, gentle textures"),
    "realistic": ("Điện ảnh bán thực", "semi-realistic digital painting, cinematic composition, volumetric lighting"),
}
DEFAULT_NEGATIVE_PROMPT = (
    "text, letters, speech bubble, watermark, signature, logo, blurry, low quality, "
    "distorted face, deformed hands, extra fingers, extra limbs"
)

ShotType = Literal["establishing", "wide", "medium", "close_up", "extreme_close_up", "over_shoulder"]
ShotMovement = Literal["static", "push_in", "pull_out", "pan_left", "pan_right", "tilt_up", "tilt_down",
                       "tracking", "handheld"]
ShotAngle = Literal["eye_level", "low", "high", "overhead"]
SHOT_TYPES: tuple[str, ...] = ShotType.__args__  # type: ignore[attr-defined]
SHOT_MOVEMENTS: tuple[str, ...] = ShotMovement.__args__  # type: ignore[attr-defined]
SHOT_ANGLES: tuple[str, ...] = ShotAngle.__args__  # type: ignore[attr-defined]
ANIMATION_PROVIDERS = ("camera_2_5d", "veo", "gpu_worker")
QUALITY_SIZES: dict[str, dict[str, tuple[int, int]]] = {
    "final": {"9:16": (1080, 1920), "16:9": (1920, 1080)},
    # Cheap preview pass before spending on full-quality generation.
    "draft": {"9:16": (720, 1280), "16:9": (1280, 720)},
}

LLM_PROVIDERS = ("auto", "gemini", "qwen3", "heuristic")
IMAGE_PROVIDERS = ("placeholder", "gemini")
MAX_STORY_CHARS = 60_000


def slugify_id(value: str, fallback: str = "character") -> str:
    ascii_text = unicodedata.normalize("NFKD", value.replace("đ", "d").replace("Đ", "D"))
    ascii_text = ascii_text.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")[:40]
    return slug or fallback


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class DialogueLine(_Model):
    character: str = Field(min_length=1, max_length=60)
    text: str = Field(min_length=1, max_length=600)

    @field_validator("character")
    @classmethod
    def _slug(cls, value: str) -> str:
        return slugify_id(value)


class ComicCharacter(_Model):
    id: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=120)
    gender: Literal["female", "male", "unknown"] = "unknown"
    age: str = Field(default="", max_length=60)
    appearance: str = Field(default="", max_length=600)
    outfit: str = Field(default="", max_length=400)
    voice: Literal["female", "male"] | None = None
    # Pinned reference image (file name in refs/). While set, every keyframe uses exactly
    # this version; editing the profile creates new variants but never replaces it.
    approved_reference: str | None = Field(default=None, max_length=120)

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        return slugify_id(value)

    @property
    def voice_gender(self) -> str | None:
        return self.voice or (self.gender if self.gender != "unknown" else None)


def _coerce_choice(value: object, choices: tuple[str, ...], aliases: dict[str, str], default: str) -> str:
    """LLMs drift on enum spelling ("Close-up", "dolly in"); map near misses, default the rest."""
    text = re.sub(r"[^a-z]+", "_", str(value or "").lower()).strip("_")
    text = aliases.get(text, text)
    return text if text in choices else default


class CameraSpec(_Model):
    shot_type: ShotType = "medium"
    movement: ShotMovement = "static"
    angle: ShotAngle = "eye_level"

    @field_validator("shot_type", mode="before")
    @classmethod
    def _type(cls, value: object) -> str:
        return _coerce_choice(value, SHOT_TYPES, {"medium_shot": "medium", "wide_shot": "wide", "closeup": "close_up",
                                                  "close": "close_up", "full_shot": "wide", "long_shot": "wide",
                                                  "extreme_closeup": "extreme_close_up"}, "medium")

    @field_validator("movement", mode="before")
    @classmethod
    def _movement(cls, value: object) -> str:
        return _coerce_choice(value, SHOT_MOVEMENTS, {"dolly_in": "push_in", "zoom_in": "push_in", "dolly_out": "pull_out",
                                                      "zoom_out": "pull_out", "track": "tracking", "follow": "tracking",
                                                      "tilt": "tilt_up", "pan": "pan_right", "none": "static"}, "static")

    @field_validator("angle", mode="before")
    @classmethod
    def _angle(cls, value: object) -> str:
        return _coerce_choice(value, SHOT_ANGLES, {"low_angle": "low", "high_angle": "high", "birds_eye": "overhead",
                                                   "top_down": "overhead", "eye": "eye_level"}, "eye_level")


class AnimationSpec(_Model):
    mode: Literal["image_to_video", "camera_2_5d"] = "image_to_video"
    motion_strength: float = Field(default=0.45, ge=0.0, le=1.0)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    # Bumped by "regenerate": a new take of the same shot gets a new cache key and seed.
    take: int = Field(default=1, ge=1, le=999)


class Shot(_Model):
    shot_index: int = Field(ge=1)
    duration_seconds: float = Field(default=5.0, ge=2.0, le=10.0)
    characters: list[str] = Field(default_factory=list, max_length=6)
    action: str = Field(default="", max_length=600)
    emotion: str = Field(default="", max_length=60)
    camera: CameraSpec = Field(default_factory=CameraSpec)
    animation: AnimationSpec = Field(default_factory=AnimationSpec)
    narration: str = Field(default="", max_length=800)
    dialogue: list[DialogueLine] = Field(default_factory=list, max_length=6)
    keyframe_prompt: str = Field(min_length=3, max_length=1200)
    motion_prompt: str = Field(default="", max_length=800)
    negative_prompt: str = Field(default="", max_length=600)
    version: int = Field(default=1, ge=1)
    keyframe_override: str | None = None

    @field_validator("characters")
    @classmethod
    def _slugs(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(slugify_id(value) for value in values if value.strip()))

    def spoken_parts(self) -> list[tuple[str | None, str]]:
        parts: list[tuple[str | None, str]] = [(None, self.narration)] if self.narration else []
        parts.extend((line.character, line.text) for line in self.dialogue)
        return parts


class ComicScene(_Model):
    scene_index: int = Field(ge=1)
    narration: str = Field(default="", max_length=1500)
    dialogue: list[DialogueLine] = Field(default_factory=list, max_length=12)
    characters: list[str] = Field(default_factory=list, max_length=8)
    location: str = Field(default="", max_length=200)
    emotion: str = Field(default="", max_length=60)
    visual_prompt: str = Field(min_length=3, max_length=1500)
    negative_prompt: str = Field(default="", max_length=600)
    camera_motion: CameraMotion = "slow_zoom_in"
    estimated_duration: float = Field(default=6.0, ge=1.0, le=40.0)
    source_excerpt: str = Field(default="", max_length=2000)
    version: int = Field(default=1, ge=1)
    image_override: str | None = None
    shots: list[Shot] = Field(default_factory=list, max_length=8)

    @field_validator("characters")
    @classmethod
    def _slugs(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(slugify_id(value) for value in values if value.strip()))

    @field_validator("camera_motion", mode="before")
    @classmethod
    def _motion(cls, value: object) -> object:
        # LLMs drift on enum spelling ("zoom in", "Pan-Left"); normalise before validating.
        text = re.sub(r"[^a-z]+", "_", str(value or "").lower()).strip("_")
        aliases = {"zoom_in": "slow_zoom_in", "zoom_out": "slow_zoom_out", "none": "static", "": "slow_zoom_in"}
        return aliases.get(text, text)

    @model_validator(mode="after")
    def _has_speech(self) -> "ComicScene":
        if not self.narration and not self.dialogue and not self.shots:
            raise ValueError(f"scene {self.scene_index} has neither narration nor dialogue")
        for position, shot in enumerate(self.shots, 1):
            shot.shot_index = position
        return self

    def shot(self, shot_index: int) -> Shot:
        for shot in self.shots:
            if shot.shot_index == shot_index:
                return shot
        raise KeyError(shot_index)

    def spoken_parts(self) -> list[tuple[str | None, str]]:
        """(character id or None for the narrator, text) in reading order."""
        parts: list[tuple[str | None, str]] = []
        if self.narration:
            parts.append((None, self.narration))
        parts.extend((line.character, line.text) for line in self.dialogue)
        return parts


class StoryboardDraft(_Model):
    """What the LLM returns for one chunk of the story."""

    characters: list[ComicCharacter] = Field(default_factory=list, max_length=30)
    scenes: list[ComicScene] = Field(min_length=1, max_length=120)


class Storyboard(_Model):
    schema_version: int = STORYBOARD_SCHEMA_VERSION
    revision: int = Field(default=1, ge=1)
    title: str = Field(default="", max_length=200)
    language: str = "vi"
    style: str = "anime_comic"
    mode: Literal["comic", "animated"] = "comic"
    analysis_method: str = "heuristic"
    characters: list[ComicCharacter] = Field(default_factory=list)
    scenes: list[ComicScene] = Field(min_length=1, max_length=400)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "Storyboard":
        ids = [character.id for character in self.characters]
        if len(ids) != len(set(ids)):
            raise ValueError("character ids must be unique")
        known = set(ids)
        for position, scene in enumerate(self.scenes, 1):
            scene.scene_index = position
            if self.mode == "animated" and not scene.shots:
                raise ValueError(f"scene {position} needs at least one shot in animated mode")
            unknown = [cid for cid in scene.characters if cid not in known]
            unknown += [line.character for line in scene.dialogue if line.character not in known]
            for shot in scene.shots:
                unknown += [cid for cid in shot.characters if cid not in known]
                unknown += [line.character for line in shot.dialogue if line.character not in known]
            if unknown:
                raise ValueError(f"scene {position} references unknown characters: {', '.join(sorted(set(unknown)))}")
        return self

    def iter_shots(self):
        for scene in self.scenes:
            for shot in scene.shots:
                yield scene, shot

    def character(self, character_id: str | None) -> ComicCharacter | None:
        return next((c for c in self.characters if c.id == character_id), None)

    def scene(self, scene_index: int) -> ComicScene:
        for scene in self.scenes:
            if scene.scene_index == scene_index:
                return scene
        raise KeyError(scene_index)


class ComicSettings(_Model):
    title: str = Field(default="Truyện chưa đặt tên", min_length=1, max_length=200)
    language: str = "vi"
    style: str = "anime_comic"
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    llm_provider: Literal["auto", "gemini", "qwen3", "heuristic"] = "auto"
    image_provider: Literal["placeholder", "gemini"] = "placeholder"
    narrator_voice: Literal["female", "male"] = "female"
    speech_rate: float = Field(default=1.0, ge=0.7, le=1.5)
    target_scene_duration: float = Field(default=7.0, ge=3.0, le=15.0)
    max_scenes: int = Field(default=40, ge=1, le=120)
    burn_subtitles: bool = True
    music_volume: float = Field(default=0.15, ge=0.0, le=1.0)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    auto_render: bool = True
    video_mode: Literal["comic", "animated"] = "comic"
    animation_provider: Literal["camera_2_5d", "veo", "gpu_worker"] = "camera_2_5d"
    quality: Literal["draft", "final"] = "final"
    fps: Literal[24, 30] = 30
    motion_strength: float = Field(default=0.45, ge=0.0, le=1.0)
    # Bounded retries and a per-render budget of AI-generated seconds: video models are expensive.
    max_animation_attempts: int = Field(default=2, ge=1, le=4)
    animation_budget_seconds: float = Field(default=240.0, ge=0.0, le=3600.0)

    @field_validator("language")
    @classmethod
    def _language(cls, value: str) -> str:
        if value == "auto":
            raise ValueError("language must be explicit for narration")
        return language_profile(value).code

    @field_validator("style")
    @classmethod
    def _style(cls, value: str) -> str:
        if value not in COMIC_STYLES:
            raise ValueError(f"style must be one of: {', '.join(COMIC_STYLES)}")
        return value

    @property
    def size(self) -> tuple[int, int]:
        return QUALITY_SIZES[self.quality][self.aspect_ratio]
