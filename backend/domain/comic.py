"""Contracts for the text-story to comic-video feature.

The storyboard is the single editable source of truth: every image, voice clip
and scene clip is derived from it and cached by a hash of the fields it uses,
so editing one scene only regenerates that scene.
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

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        return slugify_id(value)

    @property
    def voice_gender(self) -> str | None:
        return self.voice or (self.gender if self.gender != "unknown" else None)


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
        if not self.narration and not self.dialogue:
            raise ValueError(f"scene {self.scene_index} has neither narration nor dialogue")
        return self

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
            unknown = [cid for cid in scene.characters if cid not in known]
            unknown += [line.character for line in scene.dialogue if line.character not in known]
            if unknown:
                raise ValueError(f"scene {position} references unknown characters: {', '.join(sorted(set(unknown)))}")
        return self

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
        return ASPECT_RATIOS[self.aspect_ratio]
