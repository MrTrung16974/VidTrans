"""Storyboard -> character references, scene images, voice, subtitles, clips, MP4.

Every derived file is content-addressed: its name is a hash of exactly the
inputs that affect it (text, voice, prompt, reference hashes, motion, size...).
Re-rendering after an edit therefore regenerates only what the edit touched,
and a retry after a crash resumes from the last finished asset.

Dependency graph (an edit invalidates everything downstream of it):

    character appearance -> reference image -+
    scene prompt / style --------------------+-> scene image -+
    narration / dialogue -> voice -> subtitle timing ---------+-> scene clip -> final video
                                                       music ---------------------^
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from domain.comic import (
    COMIC_STYLES,
    ComicCharacter,
    ComicScene,
    ComicSettings,
    Storyboard,
    style_negative_prompt,
)
from infrastructure.comic_project import ComicProject
from infrastructure.image_providers import (
    ImageGenerationError,
    ImageProvider,
    ImageRequest,
    PlaceholderImageProvider,
    save_png_atomic,
)
from pipeline.parallel import collect_indexed_futures

logger = logging.getLogger(__name__)

FPS = 30
FADE_SECONDS = 0.35
PART_GAP_SECONDS = 0.3
SCENE_TAIL_SECONDS = 0.45
MIN_SCENE_SECONDS = 2.5
REFERENCE_SIZE = 1024
PIPELINE_VERSION = "comic-v2-manhua-style"
DEFAULT_SCENE_WORKERS = 3


def scene_workers() -> int:
    """Scenes (or shots) built at once per job: image API, TTS and FFmpeg calls overlap across them."""
    try:
        return max(1, int(os.environ.get("VIDTRANS_COMIC_CONCURRENCY", DEFAULT_SCENE_WORKERS)))
    except ValueError:
        return DEFAULT_SCENE_WORKERS


def content_hash(*values: Any) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class ComicRuntime:
    ffmpeg: str
    run_ffmpeg: Callable[[list[str]], Any]
    media_duration: Callable[[Path], float]
    # (text, output mp3, edge voice gender, rate percent, pitch Hz, language); non-Edge providers
    # also receive provider= and voice_key= keywords.
    synthesize: Callable[..., None]
    font_path: Path | None = None
    check_active: Callable[[], None] = lambda: None
    report: Callable[[str, float, str], None] = lambda step, progress, detail: None


@dataclass
class SceneResult:
    scene_index: int
    image: str
    image_hash: str
    audio_hash: str
    duration: float
    cues: list[dict[str, Any]] = field(default_factory=list)
    clip: str = ""


def character_prompt(character: ComicCharacter) -> str:
    traits = ", ".join(t for t in (character.age, character.appearance) if t)
    text = f"{character.name}" + (f" ({traits})" if traits else "")
    return text + (f", wearing {character.outfit}" if character.outfit else "")


def scene_image_prompt(scene: ComicScene, storyboard: Storyboard, aspect_ratio: str) -> str:
    style = COMIC_STYLES.get(storyboard.style, COMIC_STYLES["anime_comic"])[1]
    lines = [f"{style}.", scene.visual_prompt.rstrip(".") + "."]
    if storyboard.style == "manhua":
        lines.append("Premium romantic historical manhua illustration, soft pastel palette, delicate painterly detail, natural adult proportions, fine facial features, soft warm light, visually rich but not cartoonish, not chibi, not 3D.")
    cast = [storyboard.character(cid) for cid in scene.characters]
    described = [character_prompt(c) for c in cast if c is not None]
    if described:
        lines.append("Characters: " + "; ".join(described) + ".")
    if scene.location:
        lines.append(f"Setting: {scene.location}.")
    if scene.emotion:
        lines.append(f"Mood: {scene.emotion}.")
    lines.append(f"Single full-bleed illustration, {aspect_ratio} composition, keep the lower fifth "
                 "free of important details for subtitles, no text, no speech bubbles, no watermark.")
    return " ".join(lines)


def reference_prompt(character: ComicCharacter, style_key: str) -> str:
    style = COMIC_STYLES.get(style_key, COMIC_STYLES["anime_comic"])[1]
    return (f"{style}. Character reference sheet of {character_prompt(character)}. Full body, front view, "
            "neutral standing pose, plain light grey background, even lighting, no text.")


def split_cue_text(text: str, max_chars: int) -> list[str]:
    """Split spoken text into subtitle cues at punctuation, then at spaces."""
    pieces = [p.strip() for p in re.split(r"(?<=[.!?…;:,，。！？])\s+", text) if p.strip()]
    cues: list[str] = []
    for piece in pieces:
        if cues and len(cues[-1]) + 1 + len(piece) <= max_chars:
            cues[-1] += " " + piece
            continue
        cues.extend(_balanced_split(piece, max_chars))
    return cues


def _balanced_split(text: str, max_chars: int) -> list[str]:
    """Cut an over-long phrase into near-equal parts at word boundaries (no short orphan tail)."""
    if len(text) <= max_chars:
        return [text]
    words = text.split()
    parts = math.ceil(len(text) / max_chars)
    target = len(text) / parts
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and len(candidate) > target and len(chunks) < parts - 1:
            chunks.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    chunks.append(" ".join(current))
    return [piece for chunk in chunks for piece in (textwrap.wrap(chunk, max_chars, break_long_words=True) or [chunk])]


def timed_cues(parts: list[tuple[str, float, float]], max_chars: int) -> list[dict[str, Any]]:
    """Distribute each spoken part's duration over its cues by character count."""
    cues = []
    for text, start, end in parts:
        chunks = split_cue_text(text, max_chars)
        total = sum(len(chunk) for chunk in chunks) or 1
        cursor = start
        for chunk in chunks:
            length = (end - start) * len(chunk) / total
            cues.append({"text": chunk, "start": round(cursor, 3), "end": round(cursor + length, 3)})
            cursor += length
    return cues


def motion_expressions(motion: str, frames: int) -> tuple[str, str, str]:
    progress = f"on/{max(1, frames - 1)}"
    center_x, center_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "slow_zoom_in":
        return f"1+0.14*{progress}", center_x, center_y
    if motion == "slow_zoom_out":
        return f"1.14-0.14*{progress}", center_x, center_y
    if motion == "pan_left":
        return "1.15", f"(iw-iw/zoom)*(1-{progress})", center_y
    if motion == "pan_right":
        return "1.15", f"(iw-iw/zoom)*{progress}", center_y
    if motion == "pan_up":
        return "1.15", center_x, f"(ih-ih/zoom)*(1-{progress})"
    if motion == "pan_down":
        return "1.15", center_x, f"(ih-ih/zoom)*{progress}"
    return "1", center_x, center_y


def format_srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


class ComicVideoProducer:
    def __init__(self, project: ComicProject, settings: ComicSettings, image_provider: ImageProvider,
                 runtime: ComicRuntime) -> None:
        self.project = project
        self.settings = settings
        self.images = image_provider
        self.rt = runtime
        self.width, self.height = settings.size
        self.fps = FPS
        self.cache_hits = 0
        self.cache_misses = 0
        self.workers = scene_workers()
        self._lock = threading.Lock()
        self._report_lock = threading.Lock()
        self._file_locks: dict[str, threading.Lock] = {}
        # The image provider is called one request at a time until it has answered once, so a provider
        # that is down (quota, bad key) costs one failed request instead of one per worker.
        self._probe = threading.Lock()
        self._provider_ok = False
        # Out-of-quota image APIs must not fail the whole video: draw stand-ins and say so.
        self.image_fallback = None if image_provider.key == "placeholder" else PlaceholderImageProvider(runtime.font_path)
        self.images_down: str | None = None
        self.image_fallbacks = 0

    # ----------------------------------------------------------- concurrency
    def _cache(self, hit: bool) -> None:
        with self._lock:
            if hit:
                self.cache_hits += 1
            else:
                self.cache_misses += 1

    def _file_lock(self, path: Path) -> threading.Lock:
        """Two scenes can share a derived file (same line of dialogue, same subtitle): build it once."""
        with self._lock:
            return self._file_locks.setdefault(str(path), threading.Lock())

    def _map(self, items: list[Any], task: Callable[[Any], Any], step: str,
             describe: Callable[[int, int], str]) -> list[Any]:
        """task(item) for every item on up to ``self.workers`` threads; results keep the input order.

        The first failure (or a cancellation seen by ``check_active``) cancels the items not started yet
        and is re-raised; items already running finish their current call.
        """
        total = len(items)
        done = 0
        self.rt.report(step, 0.0, describe(0, total))

        def run(item: Any) -> Any:
            nonlocal done
            self.rt.check_active()
            result = task(item)
            with self._report_lock:
                done += 1
                self.rt.report(step, done / total, describe(done, total))
            return result

        if self.workers <= 1 or total <= 1:
            return [run(item) for item in items]
        with ThreadPoolExecutor(max_workers=min(self.workers, total), thread_name_prefix="comic") as pool:
            results = collect_indexed_futures({pool.submit(run, item): index for index, item in enumerate(items)})
        return [results[index] for index in range(total)]

    # ---------------------------------------------------------------- images
    def build_references(self, storyboard: Storyboard) -> dict[str, tuple[Path, str]]:
        refs: dict[str, tuple[Path, str]] = {}
        # Uploaded/approved references are honoured even by providers that cannot draw new ones.
        cast = [c for c in storyboard.characters if c.appearance or c.approved_reference]
        if not self.images.supports_references:
            cast = [c for c in cast if self.project.asset_path("refs", c.approved_reference or "")]
        for position, character in enumerate(cast, 1):
            self.rt.check_active()
            pinned = self.project.asset_path("refs", character.approved_reference or "")
            if pinned is not None:
                # An approved reference is used verbatim, whatever the profile says now.
                refs[character.id] = (pinned, content_hash("approved", file_hash(pinned)))
                continue
            key = content_hash(PIPELINE_VERSION, "ref", self.images.cache_key, storyboard.style,
                               character_prompt(character), self.settings.seed)
            path = self.project.dir("refs") / f"{character.id}-{key[:16]}.png"
            self.rt.report("comic_references", position / max(1, len(cast)),
                           f"Ảnh tham chiếu nhân vật {position}/{len(cast)}: {character.name}")
            generated, fallback = self._ensure_image(path, ImageRequest(
                prompt=reference_prompt(character, storyboard.style), width=REFERENCE_SIZE, height=REFERENCE_SIZE,
                negative_prompt=style_negative_prompt(storyboard.style, "multiple people, text, watermark, cropped body"),
                seed=self.settings.seed,
                aspect_ratio="1:1", caption=character.name, display_text=character_prompt(character),
            ), allow_fallback=False)
            if generated is not None and not fallback:
                refs[character.id] = (generated, key)
        return refs

    def _ensure_image(self, path: Path, request: ImageRequest, *,
                      allow_fallback: bool = True) -> tuple[Path | None, bool]:
        """(image, is_stand_in). Stand-ins get their own file so a later render still asks the real provider."""
        if path.is_file():
            self._cache(True)
            return path, False
        reason = self.images_down
        if reason is None:
            self._cache(False)
            try:
                self._generate_image(request, path)
                return path, False
            except ImageGenerationError as exc:
                if self.image_fallback is None:
                    raise
                reason = str(exc)[:200]
                logger.warning("Image provider failed (%s: %s); drawing a stand-in", type(exc).__name__, reason)
                if getattr(exc, "unavailable", False):
                    self.images_down = reason
        if not allow_fallback or self.image_fallback is None:
            return None, True
        stand_in = path.with_name(f"{path.stem}-fallback.png")
        if not stand_in.is_file():
            self.image_fallback.generate(request, stand_in)
        with self._lock:
            self.image_fallbacks += 1
            self.image_fallback_reason = reason
        return stand_in, True

    def _generate_image(self, request: ImageRequest, path: Path) -> None:
        if self._provider_ok:
            self.images.generate(request, path)
            return
        with self._probe:
            if self.images_down is None:
                self.images.generate(request, path)
                self._provider_ok = True
                return
        error = ImageGenerationError(self.images_down)
        error.unavailable = True
        raise error

    def image_report(self) -> dict[str, Any] | None:
        if not self.image_fallbacks:
            return None
        return {"count": self.image_fallbacks, "reason": getattr(self, "image_fallback_reason", "")}

    def scene_image(self, scene: ComicScene, storyboard: Storyboard,
                    refs: dict[str, tuple[Path, str]]) -> tuple[Path, str]:
        if scene.image_override:
            override = self.project.asset_path("uploads", scene.image_override)
            if override is None:
                raise FileNotFoundError(f"Ảnh tải lên của cảnh {scene.scene_index} không còn tồn tại")
            key = content_hash(PIPELINE_VERSION, "upload", file_hash(override), self.width, self.height)
            path = self.project.dir("images") / f"s{scene.scene_index:03d}-{key[:16]}.png"
            if not path.is_file():
                from PIL import Image

                from infrastructure.image_providers import fit_cover
                with Image.open(override) as image:
                    save_png_atomic(fit_cover(image, self.width, self.height), path)
            return path, key
        prompt = scene_image_prompt(scene, storyboard, self.settings.aspect_ratio)
        negative = style_negative_prompt(storyboard.style, scene.negative_prompt)
        scene_refs = [refs[cid] for cid in scene.characters if cid in refs]
        seed = self.settings.seed + scene.scene_index if self.settings.seed else 0
        key = content_hash(PIPELINE_VERSION, "scene", self.images.cache_key, prompt, negative,
                           [ref_key for _path, ref_key in scene_refs], seed, self.width, self.height)
        path = self.project.dir("images") / f"s{scene.scene_index:03d}-{key[:16]}.png"
        image, fallback = self._ensure_image(path, ImageRequest(
            prompt=prompt, width=self.width, height=self.height, negative_prompt=negative,
            seed=seed, references=tuple(ref_path for ref_path, _key in scene_refs[:3]),
            caption=f"Cảnh {scene.scene_index}", aspect_ratio=self.settings.aspect_ratio,
            display_text=scene.narration or scene.visual_prompt,
        ))
        # A distinct key keeps clips built on a stand-in from being reused once the real image exists.
        return image, f"{key}-fallback" if fallback else key

    # ----------------------------------------------------------------- audio
    def _voice_for(self, character_id: str | None, storyboard: Storyboard) -> tuple[str, int]:
        narrator = self.settings.narrator_voice
        if character_id is None:
            return narrator, 0
        character = storyboard.character(character_id)
        gender = (character.voice_gender if character else None) or narrator
        # Dialogue in the narrator's voice gets a small pitch lift so it reads as a different speaker.
        return gender, (6 if gender == narrator else 0)

    def _provider_voice(self, character_id: str | None, gender: str) -> list[str] | None:
        """Provider and voice id for non-Edge narration; None keeps Edge cache keys unchanged."""
        if self.settings.tts_provider == "edge":
            return None
        voices = self.settings.tts_voices
        # The narrator has its own voice, so dialogue stays distinguishable without Edge's pitch lift.
        voice = voices.get("narrator") if character_id is None else voices.get(gender)
        return [self.settings.tts_provider, voice or voices.get(gender) or ""]

    def scene_audio(self, scene: ComicScene, storyboard: Storyboard) -> tuple[Path, str, float, list[dict]]:
        return self.speech_audio(f"s{scene.scene_index:03d}", scene.spoken_parts(), storyboard)

    def speech_audio(self, label: str, spoken: list[tuple[str | None, str]], storyboard: Storyboard,
                     min_duration: float = MIN_SCENE_SECONDS) -> tuple[Path, str, float, list[dict]]:
        rate = int(round((self.settings.speech_rate - 1.0) * 100))
        part_specs = []
        for character_id, text in spoken:
            gender, pitch = self._voice_for(character_id, storyboard)
            voice = self._provider_voice(character_id, gender)
            part_specs.append((text, gender, pitch) if voice is None else (text, gender, pitch, voice))
        extra = [] if (min_duration == MIN_SCENE_SECONDS and self.fps == FPS) else [min_duration, self.fps]
        key = content_hash(PIPELINE_VERSION, "audio", self.settings.language, rate, part_specs, *extra)
        audio_dir = self.project.dir("audio")
        wav = audio_dir / f"{label}-{key[:16]}.wav"
        meta_path = wav.with_suffix(".json")
        if wav.is_file() and meta_path.is_file():
            self._cache(True)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return wav, key, float(meta["duration"]), meta["parts"]
        self._cache(False)
        timeline: list[dict[str, Any]] = []
        part_wavs: list[Path] = []
        cursor = 0.0
        for position, (text, gender, pitch, *voice) in enumerate(part_specs):
            self.rt.check_active()
            part_key = content_hash(PIPELINE_VERSION, "voice", self.settings.language, rate, text, gender, pitch, *voice)
            provider_kwargs = {"provider": voice[0][0], "voice_key": voice[0][1]} if voice else {}
            mp3 = audio_dir / f"part-{part_key[:20]}.mp3"
            part_wav = mp3.with_suffix(".wav")
            with self._file_lock(part_wav):
                if not part_wav.is_file():
                    self.rt.synthesize(text, mp3, gender, rate, pitch, self.settings.language, **provider_kwargs)
                    temporary = part_wav.with_suffix(".tmp.wav")
                    self.rt.run_ffmpeg([
                        self.rt.ffmpeg, "-y", "-i", str(mp3), "-af",
                        "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-50dB,areverse,"
                        "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-50dB,areverse",
                        "-ac", "1", "-ar", "48000", str(temporary),
                    ])
                    temporary.replace(part_wav)
            duration = self.rt.media_duration(part_wav)
            if position:
                cursor += PART_GAP_SECONDS
            timeline.append({"text": text, "start": round(cursor, 3), "end": round(cursor + duration, 3)})
            part_wavs.append(part_wav)
            cursor += duration
        # Whole frames: clip lengths then add up exactly, so the joined SRT never drifts.
        spoken_end = cursor + SCENE_TAIL_SECONDS if part_wavs else 0.0
        total = math.ceil(max(min_duration, spoken_end) * self.fps) / self.fps
        if not part_wavs:  # A silent shot still needs an audio track to concatenate.
            self.rt.run_ffmpeg([self.rt.ffmpeg, "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                                "-t", f"{total:.3f}", str(wav)])
            meta_path.write_text(json.dumps({"duration": total, "parts": []}), "utf-8")
            return wav, key, total, []
        inputs: list[str] = []
        labels = []
        for index, part_wav in enumerate(part_wavs):
            inputs += ["-i", str(part_wav)]
            delay = int(round(timeline[index]["start"] * 1000))
            labels.append(f"[{index}:a]adelay={delay}:all=1[p{index}]")
        mix = ";".join(labels) + ";" + "".join(f"[p{i}]" for i in range(len(part_wavs)))
        mix += f"amix=inputs={len(part_wavs)}:duration=longest:normalize=0,apad=whole_dur={total:.3f}[a]"
        self.rt.run_ffmpeg([self.rt.ffmpeg, "-y", *inputs, "-filter_complex", mix, "-map", "[a]",
                            "-t", f"{total:.3f}", "-ac", "1", "-ar", "48000", str(wav)])
        meta_path.write_text(json.dumps({"duration": total, "parts": timeline}, ensure_ascii=False), "utf-8")
        return wav, key, total, timeline

    # ------------------------------------------------------------- subtitles
    def _font(self, size: int):
        from PIL import ImageFont

        if self.rt.font_path and Path(self.rt.font_path).is_file():
            return ImageFont.truetype(str(self.rt.font_path), size)
        return ImageFont.load_default(size=size)

    @property
    def cue_chars(self) -> int:
        # Two subtitle lines per cue.
        return 40 if self.height > self.width else 64

    def subtitle_overlay(self, text: str) -> Path:
        key = content_hash(PIPELINE_VERSION, "sub", text, self.width, self.height)
        path = self.project.dir("subs") / f"{key[:24]}.png"
        with self._file_lock(path):
            if not path.is_file():
                self._draw_subtitle(text, path)
        return path

    def _draw_subtitle(self, text: str, path: Path) -> None:
        from PIL import Image, ImageDraw

        font_size = round(min(self.width, self.height) * (0.052 if self.height > self.width else 0.045))
        font = self._font(font_size)
        image = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        lines = _balanced_split(text, max(10, self.cue_chars // 2 + 2))
        line_height = round(font_size * 1.3)
        block = line_height * len(lines)
        bottom = round(self.height * (0.86 if self.height > self.width else 0.9))
        y = bottom - block
        pad = round(font_size * 0.45)
        widths = [draw.textlength(line, font=font) for line in lines]
        box_w = max(widths) + 2 * pad
        draw.rounded_rectangle(((self.width - box_w) / 2, y - pad, (self.width + box_w) / 2, bottom + pad * 0.6),
                               radius=pad, fill=(0, 0, 0, 150))
        for line, width in zip(lines, widths):
            draw.text(((self.width - width) / 2, y), line, font=font, fill=(255, 255, 255, 255),
                      stroke_width=max(1, font_size // 16), stroke_fill=(0, 0, 0, 255))
            y += line_height
        save_png_atomic(image, path)

    # ------------------------------------------------------------------ clip
    def scene_clip(self, scene: ComicScene, image: Path, image_key: str, audio: Path, audio_key: str,
                   duration: float, cues: list[dict[str, Any]]) -> Path:
        overlays = [(self.subtitle_overlay(c["text"]), c["start"], c["end"]) for c in cues] \
            if self.settings.burn_subtitles else []
        key = content_hash(PIPELINE_VERSION, "clip", image_key, audio_key, scene.camera_motion, self.width,
                           self.height, FPS, [(p.name, s, e) for p, s, e in overlays])
        clip = self.project.dir("clips") / f"s{scene.scene_index:03d}-{key[:16]}.mp4"
        if clip.is_file():
            self._cache(True)
            return clip
        self._cache(False)
        frames = max(1, round(duration * FPS))
        zoom, x, y = motion_expressions(scene.camera_motion, frames)
        inputs = ["-i", str(image), "-i", str(audio)]
        graph = [f"[0:v]scale={self.width * 2}:{self.height * 2},zoompan=z='{zoom}':x='{x}':y='{y}'"
                 f":d={frames}:s={self.width}x{self.height}:fps={FPS},setsar=1,format=yuv420p[v0]"]
        current = "v0"
        for index, (overlay, start, end) in enumerate(overlays):
            inputs += ["-i", str(overlay)]
            graph.append(f"[{current}][{index + 2}:v]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'[v{index + 1}]")
            current = f"v{index + 1}"
        fade_out = max(0.0, duration - FADE_SECONDS)
        graph.append(f"[{current}]fade=t=in:st=0:d={FADE_SECONDS},fade=t=out:st={fade_out:.3f}:d={FADE_SECONDS}[v]")
        graph.append(f"[1:a]apad,atrim=0:{duration:.3f},afade=t=out:st={max(0.0, duration - 0.3):.3f}:d=0.3,"
                     "aformat=sample_rates=48000:channel_layouts=stereo[a]")
        temporary = clip.with_suffix(".tmp.mp4")
        self.rt.run_ffmpeg([
            self.rt.ffmpeg, "-y", *inputs, "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
            "-t", f"{duration:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(temporary),
        ])
        temporary.replace(clip)
        return clip

    # ----------------------------------------------------------------- final
    def concat(self, clips: list[Path], output: Path, music: Path | None) -> None:
        work = self.project.dir("render")
        listing = work / "clips.txt"
        listing.write_text("".join("file '" + str(c.resolve()).replace("'", "'\\''") + "'\n" for c in clips),
                           encoding="utf-8")
        joined = work / "joined.mp4" if music else output
        self.rt.run_ffmpeg([self.rt.ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                            "-c", "copy", "-movflags", "+faststart", str(joined)])
        if music:
            self.rt.run_ffmpeg([
                self.rt.ffmpeg, "-y", "-i", str(joined), "-stream_loop", "-1", "-i", str(music),
                "-filter_complex",
                f"[1:a]volume={self.settings.music_volume:.3f},aformat=sample_rates=48000:channel_layouts=stereo[m];"
                "[0:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", str(output),
            ])

    def produce(self, storyboard: Storyboard, output_video: Path, output_srt: Path,
                music: Path | None = None) -> dict[str, Any]:
        scenes = storyboard.scenes
        self.rt.report("comic_references", 0.0, "Chuẩn bị nhân vật")
        refs = self.build_references(storyboard)
        # Each scene runs image -> voice -> clip; several scenes run at once so API waits and encodes overlap.
        results: list[SceneResult] = self._map(
            scenes, lambda scene: self._render_scene(scene, storyboard, refs), "comic_clips",
            lambda done, total: f"Đã dựng {done}/{total} cảnh (ảnh, giọng đọc, clip)")
        clips = [self.project.root / "clips" / result.clip for result in results]
        self.rt.check_active()
        self.rt.report("comic_export", 0.0, "Ghép video và nhạc nền")
        output_video.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_video.with_suffix(".tmp.mp4")
        self.concat(clips, temporary, music)
        temporary.replace(output_video)
        self._write_srt(results, output_srt)
        manifest = {
            "revision": storyboard.revision,
            "size": [self.width, self.height],
            "duration": round(sum(r.duration for r in results), 3),
            "image_provider": self.images.key,
            "references": {cid: path.name for cid, (path, _key) in refs.items()},
            "scenes": {str(r.scene_index): {"image": r.image, "duration": round(r.duration, 3),
                                            "clip": r.clip, "cues": r.cues} for r in results},
            "cache": {"hits": self.cache_hits, "misses": self.cache_misses},
            "image_fallback": self.image_report(),
        }
        self.project.save_manifest(manifest)
        return manifest

    def preview_images(self, storyboard: Storyboard) -> dict[str, Any]:
        """Generate only references and scene images (storyboard review before spending on voice/render)."""
        refs = self.build_references(storyboard)
        images = self._map(storyboard.scenes, lambda scene: self.scene_image(scene, storyboard, refs)[0],
                           "comic_images", lambda done, total: f"Đã sinh ảnh {done}/{total} cảnh")
        scenes = {str(scene.scene_index): {"image": image.name} for scene, image in zip(storyboard.scenes, images)}
        manifest = {"revision": storyboard.revision, "size": [self.width, self.height],
                    "image_provider": self.images.key,
                    "references": {cid: path.name for cid, (path, _key) in refs.items()}, "scenes": scenes,
                    "cache": {"hits": self.cache_hits, "misses": self.cache_misses},
                    "image_fallback": self.image_report()}
        self.project.save_manifest(manifest)
        return manifest

    def _render_scene(self, scene: ComicScene, storyboard: Storyboard,
                      refs: dict[str, tuple[Path, str]]) -> SceneResult:
        image, image_key = self.scene_image(scene, storyboard, refs)
        _wav, audio_key, duration, parts = self.scene_audio(scene, storyboard)
        cues = timed_cues([(p["text"], p["start"], p["end"]) for p in parts], self.cue_chars)
        audio = self.project.root / "audio" / f"s{scene.scene_index:03d}-{audio_key[:16]}.wav"
        clip = self.scene_clip(scene, image, image_key, audio, audio_key, duration, cues)
        return SceneResult(scene.scene_index, image.name, image_key, audio_key, duration, cues, clip.name)

    def _write_srt(self, results: list[SceneResult], output: Path) -> None:
        blocks, offset, number = [], 0.0, 1
        for result in results:
            for cue in result.cues:
                blocks.append(f"{number}\n{format_srt_time(offset + cue['start'])} --> "
                              f"{format_srt_time(offset + cue['end'])}\n{cue['text']}\n")
                number += 1
            offset += result.duration
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(blocks), encoding="utf-8")


def remove_stale_assets(project: ComicProject, manifest: dict[str, Any]) -> None:
    """Keep the cache bounded by dropping clips no current scene uses.

    Images and voice are kept: they are the costly API calls, and reverting an edit reuses them.
    """
    entries = [*manifest.get("scenes", {}).values(), *manifest.get("shots", {}).values(),
               *manifest.get("previews", {}).values()]
    keep = {entry.get("clip") for entry in entries}
    for pattern in ("s*-*.mp4", "c*-*.mp4"):
        for path in (project.root / "clips").glob(pattern):
            if path.name not in keep:
                path.unlink(missing_ok=True)
    shutil.rmtree(project.root / "render", ignore_errors=True)
