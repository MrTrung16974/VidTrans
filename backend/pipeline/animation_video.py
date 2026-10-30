"""Animated storyboard -> keyframes -> image-to-video shots -> MP4.

Per shot (all content-addressed, see ``comic_video``):

    approved reference -+
    keyframe prompt ----+-> keyframe -+
    motion prompt / seed / take ------+-> animation (QC, bounded retries, 2.5D fallback) -+
    narration / dialogue -> voice (sets the shot length) -> subtitle cues ----------------+-> shot clip
                                                                                       -> final video

The voice is generated before the animation because it fixes how long the
shot must last; the clip is then slowed slightly or held on its last frame
(never stretched more than 25 %) to match.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domain.comic import (
    COMIC_STYLES,
    LIVE_ACTION_STYLES,
    ComicScene,
    ComicSettings,
    Shot,
    Storyboard,
    style_negative_prompt,
)
from infrastructure.comic_project import ComicProject
from infrastructure.image_providers import ImageProvider, ImageRequest, fit_cover, save_png_atomic
from infrastructure.video_providers import VideoGenerationError, VideoProvider, VideoRequest, animation_slot
from pipeline.animation_qc import check_clip
from pipeline.comic_video import (
    FADE_SECONDS,
    MIN_SCENE_SECONDS,
    ComicRuntime,
    ComicVideoProducer,
    SCENE_COMPOSITION,
    SceneResult,
    cast_description,
    cast_negative,
    PLACE_KEY,
    scene_setting,
    split_references,
    world_line,
    content_hash,
    file_hash,
    scene_negative,
    timed_cues,
    without_names,
)

logger = logging.getLogger(__name__)

ANIMATION_VERSION = "anim-v1"
MAX_SLOWDOWN = 1.25
MAX_SHOT_SECONDS = 10.0

SHOT_FRAMING = {
    "establishing": "establishing shot, very wide view of the whole location",
    "wide": "wide shot, full-body figures within the location", "medium": "medium shot, waist up, surroundings visible", "close_up": "close-up on the face", "extreme_close_up": "extreme close-up",
    "over_shoulder": "over-the-shoulder shot",
}
CLOSE_SHOTS = frozenset({"close_up", "extreme_close_up"})
SHOT_ANGLE = {"eye_level": "eye-level camera", "low": "low-angle camera", "high": "high-angle camera",
              "overhead": "overhead top-down camera"}
CAMERA_MOVES = {
    "static": "static camera", "push_in": "slow push-in toward the subject", "pull_out": "slow pull-out",
    "pan_left": "slow pan to the left", "pan_right": "slow pan to the right", "tilt_up": "slow tilt up",
    "tilt_down": "slow tilt down", "tracking": "smooth tracking shot following the subject",
    "handheld": "subtle handheld camera",
}


def shot_label(scene: ComicScene, shot: Shot) -> str:
    return f"{scene.scene_index}.{shot.shot_index}"


def keyframe_prompt(scene: ComicScene, shot: Shot, storyboard: Storyboard, aspect_ratio: str) -> str:
    style = COMIC_STYLES.get(storyboard.style, COMIC_STYLES["anime_comic"])[1]
    live_action = storyboard.style in LIVE_ACTION_STYLES
    medium = "Cinematic film keyframe" if live_action else "2D animation keyframe"
    # The image model weighs the first ~77 tokens most: action and subject lead, style comes last.
    parts = [without_names(shot.keyframe_prompt, storyboard).rstrip(".") + ".",
             f"{SHOT_FRAMING[shot.camera.shot_type]}, {SHOT_ANGLE[shot.camera.angle]}."]
    # The place comes before the cast so it survives the token budget and the frame is not a lone portrait.
    setting = scene_setting(scene, storyboard) or without_names(scene.visual_prompt, storyboard)
    if setting:
        parts.append(f"Setting: {setting[:400].rstrip('.')}.")
    if storyboard.world:
        parts.append(world_line(storyboard))
    if shot.camera.shot_type not in CLOSE_SHOTS:
        parts.append(f"{SCENE_COMPOSITION}.")
    cast = cast_description(storyboard, shot.characters)
    if cast:
        parts.append(cast)
    if shot.emotion or scene.emotion:
        parts.append(f"Mood: {shot.emotion or scene.emotion}.")
    frame = "clean film frame" if live_action else "clean animation-ready frame"
    parts.append(f"{medium}, {style}. {aspect_ratio} composition, {frame}, no text, no speech bubbles, no watermark.")
    return " ".join(parts)


def motion_prompt(scene: ComicScene, shot: Shot, storyboard: Storyboard) -> str:
    style = COMIC_STYLES.get(storyboard.style, COMIC_STYLES["anime_comic"])[1]
    strength = shot.animation.motion_strength
    energy = "very subtle, minimal motion" if strength < 0.3 else "dynamic, energetic motion" if strength > 0.7 \
        else "natural, moderate motion"
    action = shot.motion_prompt or shot.action or "subtle natural motion"
    medium, smooth = (("live-action shot", "smooth natural cinematic motion") if storyboard.style in LIVE_ACTION_STYLES
                      else ("animation", "smooth fluid animation"))
    return (f"{style} {medium}. {action.rstrip('.')}. {CAMERA_MOVES[shot.camera.movement]}, {energy}. "
            "Keep every character's face, hair and outfit identical to the first frame; stable anatomy; "
            f"{smooth}; no text, no scene cut.")


@dataclass
class PreparedShot:
    """Keyframe and voice of a shot: everything its animation needs, built before any motion is paid for."""
    keyframe: Path
    keyframe_key: str
    audio: Path
    audio_key: str
    duration: float
    cues: list[dict[str, Any]]


@dataclass
class AnimationOutcome:
    path: Path
    key: str
    meta: dict[str, Any]


class AnimatedVideoProducer(ComicVideoProducer):
    def __init__(self, project: ComicProject, settings: ComicSettings, image_provider: ImageProvider,
                 video_provider: VideoProvider, fallback_provider: VideoProvider, runtime: ComicRuntime) -> None:
        super().__init__(project, settings, image_provider, runtime)
        self.fps = settings.fps
        self.video = video_provider
        self.fallback = fallback_provider
        self.ai_seconds = 0.0
        self.attempts = 0
        self.fallbacks = 0

    # --------------------------------------------------------------- keyframe
    def shot_keyframe(self, scene: ComicScene, shot: Shot, storyboard: Storyboard,
                      refs: dict[str, tuple[Path, str]]) -> tuple[Path, str]:
        stem = f"k{scene.scene_index:03d}-{shot.shot_index:02d}"
        if shot.keyframe_override:
            override = self.project.asset_path("uploads", shot.keyframe_override)
            if override is None:
                raise FileNotFoundError(f"Keyframe tải lên của shot {shot_label(scene, shot)} không còn tồn tại")
            key = content_hash(ANIMATION_VERSION, "upload", file_hash(override), self.width, self.height)
            path = self.project.dir("keyframes") / f"{stem}-{key[:16]}.png"
            if not path.is_file():
                from PIL import Image

                with Image.open(override) as image:
                    save_png_atomic(fit_cover(image, self.width, self.height), path)
            return path, key
        prompt = keyframe_prompt(scene, shot, storyboard, self.settings.aspect_ratio)
        shot_refs = [refs[cid] for cid in shot.characters if cid in refs]
        place_ref = refs.get(PLACE_KEY + scene.location_id) if scene.location_id else None
        seed = self._seed(shot, f"{scene.scene_index}.{shot.shot_index}") if self.settings.seed else 0
        negative = cast_negative(storyboard, shot.characters, scene_negative(
            style_negative_prompt(storyboard.style, shot.negative_prompt), shot.camera.shot_type in CLOSE_SHOTS))
        key = content_hash(ANIMATION_VERSION, "keyframe", self.images.cache_key, prompt, negative,
                           [ref_key for _p, ref_key in shot_refs], seed, self.width, self.height,
                           *((place_ref[1],) if place_ref else ()))
        path = self.project.dir("keyframes") / f"{stem}-{key[:16]}.png"
        image, fallback = self._ensure_image(path, ImageRequest(
            prompt=prompt, width=self.width, height=self.height, negative_prompt=negative, seed=seed,
            references=tuple(ref_path for ref_path, _k in shot_refs[:3]), aspect_ratio=self.settings.aspect_ratio,
            setting_reference=place_ref[0] if place_ref else None,
            caption=f"Shot {shot_label(scene, shot)}", display_text=shot.narration or shot.action or shot.keyframe_prompt,
        ))
        return image, f"{key}-fallback" if fallback else key

    def _seed(self, shot: Shot, label: str) -> int:
        base = shot.animation.seed or self.settings.seed or int(content_hash("seed", label)[:8], 16)
        return (base + (shot.animation.take - 1) * 104_729) % (2**31 - 1) or 1

    # -------------------------------------------------------------- animation
    def _normalize(self, raw: Path, output: Path, duration: float) -> None:
        """Exact size, fps and length; slow down at most MAX_SLOWDOWN, then hold the last frame."""
        source = max(0.1, float(self.rt.media_duration(raw) or 0.1))
        slow = min(MAX_SLOWDOWN, duration / source) if source < duration else 1.0
        hold = max(0.0, duration - source * slow) + 0.2
        frames = max(1, round(duration * self.fps))
        w, h = self.width, self.height
        graph = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
                 f"setpts={slow:.4f}*PTS,fps={self.fps},tpad=stop_mode=clone:stop_duration={hold:.3f},format=yuv420p")
        temporary = output.with_suffix(".tmp.mp4")
        self.rt.run_ffmpeg([self.rt.ffmpeg, "-y", "-i", str(raw), "-an", "-vf", graph, "-frames:v", str(frames),
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                            str(temporary)])
        temporary.replace(output)

    def _request(self, scene: ComicScene, shot: Shot, storyboard: Storyboard, keyframe: Path, duration: float,
                 seed: int, key: str) -> VideoRequest:
        return VideoRequest(
            keyframe=keyframe, prompt=motion_prompt(scene, shot, storyboard), duration_seconds=duration,
            width=self.width, height=self.height, fps=self.fps, aspect_ratio=self.settings.aspect_ratio,
            negative_prompt=shot.negative_prompt, seed=seed, motion_strength=shot.animation.motion_strength,
            camera_movement=shot.camera.movement, idempotency_key=key,
        )

    def shot_animation(self, scene: ComicScene, shot: Shot, storyboard: Storyboard, keyframe: Path,
                       keyframe_key: str, duration: float) -> AnimationOutcome:
        provider = self.video if shot.animation.mode == "image_to_video" else self.fallback
        label = shot_label(scene, shot)
        seed = self._seed(shot, label)
        prompt = motion_prompt(scene, shot, storyboard)
        key = content_hash(ANIMATION_VERSION, "animation", provider.cache_key, keyframe_key, prompt,
                           shot.negative_prompt, round(duration, 3), self.fps, self.width, self.height,
                           shot.animation.motion_strength, shot.camera.movement, seed, shot.animation.take,
                           self.settings.max_animation_attempts)
        path = self.project.dir("animations") / f"a{scene.scene_index:03d}-{shot.shot_index:02d}-{key[:16]}.mp4"
        meta_path = path.with_suffix(".json")
        if path.is_file() and meta_path.is_file():
            self._cache(True)
            return AnimationOutcome(path, key, json.loads(meta_path.read_text(encoding="utf-8")))
        self._cache(False)
        attempts: list[dict[str, Any]] = []
        meta: dict[str, Any] | None = None
        if provider.is_ai:
            generation = provider.generation_duration(duration)
            for attempt in range(self.settings.max_animation_attempts):
                self.rt.check_active()
                if self.ai_seconds + generation > self.settings.animation_budget_seconds:
                    attempts.append({"attempt": attempt + 1, "skipped": "Vượt ngân sách AI của lần render"})
                    break
                attempt_seed = (seed + attempt * 7919) % (2**31 - 1) or 1
                raw = path.with_suffix(f".raw{attempt}.mp4")
                started = time.monotonic()
                self.attempts += 1
                try:
                    with animation_slot(self.rt.check_active,
                                        on_wait=lambda: self.rt.report("comic_animation", self._progress,
                                                                       f"Shot {label}: đang chờ GPU trống")):
                        result = provider.generate(self._request(scene, shot, storyboard, keyframe, generation,
                                                                 attempt_seed, f"{key}-{attempt}"),
                                                   raw, self.rt.check_active)
                except VideoGenerationError as exc:
                    attempts.append({"attempt": attempt + 1, "seed": attempt_seed, "error": str(exc)[:200],
                                     "elapsed": round(time.monotonic() - started, 2)})
                    raw.unlink(missing_ok=True)
                    if not exc.retryable:
                        break
                    continue
                self.ai_seconds += result.billed_seconds
                qc = check_clip(raw, min(generation, duration), ffmpeg=self.rt.ffmpeg, run_ffmpeg=self.rt.run_ffmpeg,
                                media_duration=self.rt.media_duration)
                attempts.append({"attempt": attempt + 1, "seed": attempt_seed, "qc": qc.as_dict(),
                                 "billed_seconds": result.billed_seconds,
                                 "elapsed": round(time.monotonic() - started, 2)})
                if qc.passed:
                    self._normalize(raw, path, duration)
                    raw.unlink(missing_ok=True)
                    meta = {"provider": provider.key, "model": result.model, "seed": attempt_seed, "fallback": False,
                            "qc": qc.as_dict(), "attempts": attempts, "billed_seconds": result.billed_seconds}
                    break
                raw.unlink(missing_ok=True)
        if meta is None:
            # 2.5D camera move: never as rich as AI motion, but the shot always renders.
            raw = path.with_suffix(".raw.mp4")
            result = self.fallback.generate(self._request(scene, shot, storyboard, keyframe, duration, seed, key),
                                            raw, self.rt.check_active)
            self._normalize(raw, path, duration)
            raw.unlink(missing_ok=True)
            reason = None
            if provider.is_ai:
                self.fallbacks += 1
                reason = attempts[-1].get("error") or attempts[-1].get("skipped") or "; ".join(
                    attempts[-1].get("qc", {}).get("reasons", [])) if attempts else "Không có lượt thử nào"
            meta = {"provider": self.fallback.key, "model": result.model, "seed": seed,
                    "fallback": provider.is_ai, "fallback_reason": reason, "attempts": attempts, "billed_seconds": 0.0}
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return AnimationOutcome(path, key, meta)

    # ------------------------------------------------------------------- clip
    def shot_clip(self, scene: ComicScene, shot: Shot, animation: AnimationOutcome, audio: Path, audio_key: str,
                  duration: float, cues: list[dict[str, Any]]) -> Path:
        overlays = [(self.subtitle_overlay(c["text"]), c["start"], c["end"]) for c in cues] \
            if self.settings.burn_subtitles else []
        # Hard cuts between shots of a scene; fades only at scene boundaries.
        fade_in = shot.shot_index == 1
        fade_out = shot.shot_index == len(scene.shots)
        key = content_hash(ANIMATION_VERSION, "clip", animation.key, audio_key, self.width, self.height, self.fps,
                           fade_in, fade_out, [(p.name, s, e) for p, s, e in overlays])
        clip = self.project.dir("clips") / f"c{scene.scene_index:03d}-{shot.shot_index:02d}-{key[:16]}.mp4"
        if clip.is_file():
            self._cache(True)
            return clip
        self._cache(False)
        inputs = ["-i", str(animation.path), "-i", str(audio)]
        graph = ["[0:v]setsar=1,format=yuv420p[v0]"]
        current = "v0"
        for index, (overlay, start, end) in enumerate(overlays):
            inputs += ["-i", str(overlay)]
            graph.append(f"[{current}][{index + 2}:v]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'[v{index + 1}]")
            current = f"v{index + 1}"
        fades = []
        if fade_in:
            fades.append(f"fade=t=in:st=0:d={FADE_SECONDS}")
        if fade_out:
            fades.append(f"fade=t=out:st={max(0.0, duration - FADE_SECONDS):.3f}:d={FADE_SECONDS}")
        graph.append(f"[{current}]{','.join(fades) or 'null'}[v]")
        graph.append(f"[1:a]apad,atrim=0:{duration:.3f},aformat=sample_rates=48000:channel_layouts=stereo[a]")
        temporary = clip.with_suffix(".tmp.mp4")
        self.rt.run_ffmpeg([
            self.rt.ffmpeg, "-y", *inputs, "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
            "-t", f"{duration:.3f}", "-r", str(self.fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(temporary),
        ])
        temporary.replace(clip)
        return clip

    # --------------------------------------------------------------- pipeline
    _progress = 0.0

    def _prepare_shot(self, scene: ComicScene, shot: Shot, storyboard: Storyboard,
                      refs: dict[str, tuple[Path, str]]) -> PreparedShot:
        keyframe, keyframe_key = self.shot_keyframe(scene, shot, storyboard, refs)
        spoken = shot.spoken_parts()
        audio, audio_key, duration, parts = self.speech_audio(
            f"t{scene.scene_index:03d}-{shot.shot_index:02d}", spoken, storyboard,
            # A spoken shot lasts as long as its voice: padding to the storyboard estimate left up to
            # 1.5 s of silence between shots. Silent shots keep their requested length.
            min_duration=MIN_SCENE_SECONDS if spoken else shot.duration_seconds)
        cues = timed_cues([(p["text"], p["start"], p["end"]) for p in parts], self.cue_chars)
        return PreparedShot(keyframe, keyframe_key, audio, audio_key, duration, cues)

    def _shot_entry(self, scene: ComicScene, shot: Shot, prepared: PreparedShot, animation: AnimationOutcome,
                    clip: Path) -> dict[str, Any]:
        return {"keyframe": prepared.keyframe.name, "animation": animation.path.name, "clip": clip.name,
                "duration": round(prepared.duration, 3), "cues": prepared.cues,
                "provider": animation.meta.get("provider"), "model": animation.meta.get("model"),
                "fallback": animation.meta.get("fallback", False),
                "fallback_reason": animation.meta.get("fallback_reason"), "seed": animation.meta.get("seed"),
                "qc": animation.meta.get("qc"), "attempts": len(animation.meta.get("attempts") or []),
                "billed_seconds": animation.meta.get("billed_seconds", 0.0),
                "too_long": prepared.duration > MAX_SHOT_SECONDS}

    def _render_shots(self, pairs: list[tuple[ComicScene, Shot]], storyboard: Storyboard,
                      refs: dict[str, tuple[Path, str]]) -> dict[str, Any]:
        """Keyframes + voices in parallel, AI motion one shot at a time in story order, clips in parallel.

        Motion stays sequential: the GPU slot serialises it anyway, and the AI budget then goes to the
        earliest shots exactly as before, whatever the thread timing.
        """
        prepared: list[PreparedShot] = self._map(
            pairs, lambda pair: self._prepare_shot(*pair, storyboard, refs), "comic_keyframes",
            lambda done, total: f"Đã có keyframe và giọng đọc {done}/{total} shot")
        animations: list[AnimationOutcome] = []
        for position, ((scene, shot), ready) in enumerate(zip(pairs, prepared), 1):
            self.rt.check_active()
            self._progress = (position - 1) / len(pairs)
            self.rt.report("comic_animation", self._progress,
                           f"Tạo chuyển động shot {shot_label(scene, shot)} ({position}/{len(pairs)})")
            animations.append(self.shot_animation(scene, shot, storyboard, ready.keyframe, ready.keyframe_key,
                                                  ready.duration))
        jobs = list(zip(pairs, prepared, animations))
        clips: list[Path] = self._map(
            jobs, lambda job: self.shot_clip(*job[0], job[2], job[1].audio, job[1].audio_key, job[1].duration,
                                             job[1].cues),
            "comic_clips", lambda done, total: f"Đã ghép hình, tiếng, phụ đề {done}/{total} shot")
        return {shot_label(scene, shot): self._shot_entry(scene, shot, ready, animation, clip)
                for (scene, shot), ready, animation, clip in zip(pairs, prepared, animations, clips)}

    def _manifest(self, storyboard: Storyboard, refs: dict[str, tuple[Path, str]], shots: dict[str, Any],
                  **extra: Any) -> dict[str, Any]:
        return {
            "mode": "animated", "revision": storyboard.revision, "size": [self.width, self.height], "fps": self.fps,
            "image_provider": self.images.key, "animation_provider": self.video.key,
            **split_references(refs), "shots": shots,
            "metrics": {"ai_seconds": round(self.ai_seconds, 2), "attempts": self.attempts,
                        "fallback_shots": sum(1 for s in shots.values() if s.get("fallback"))},
            "cache": {"hits": self.cache_hits, "misses": self.cache_misses},
            "image_fallback": self.image_report(), **extra,
        }

    def produce(self, storyboard: Storyboard, output_video: Path, output_srt: Path,
                music: Path | None = None) -> dict[str, Any]:
        pairs = list(storyboard.iter_shots())
        self.rt.report("comic_references", 0.0, "Chuẩn bị nhân vật")
        refs = self.build_references(storyboard)
        shots = self._render_shots(pairs, storyboard, refs)
        self.rt.check_active()
        self.rt.report("comic_export", 0.0, "Ghép các shot và nhạc nền")
        clips = [self.project.root / "clips" / entry["clip"] for entry in shots.values()]
        output_video.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_video.with_suffix(".tmp.mp4")
        self.concat(clips, temporary, music)
        temporary.replace(output_video)
        self._write_srt([SceneResult(0, "", "", "", entry["duration"], entry["cues"]) for entry in shots.values()],
                        output_srt)
        manifest = self._manifest(storyboard, refs, shots,
                                  duration=round(sum(e["duration"] for e in shots.values()), 3))
        self.project.save_manifest(manifest)
        return manifest

    def preview_images(self, storyboard: Storyboard, scene_limit: int | None = None) -> dict[str, Any]:
        """Keyframes only: review composition and characters before paying for motion."""
        targets = storyboard.scenes[:scene_limit] if scene_limit else storyboard.scenes
        pairs = [(scene, shot) for scene in targets for shot in scene.shots]
        refs = self.build_references(storyboard, targets if scene_limit else None)
        keyframes = self._map(pairs, lambda pair: self.shot_keyframe(*pair, storyboard, refs)[0], "comic_keyframes",
                              lambda done, total: f"Đã sinh keyframe {done}/{total} shot")
        shots = {shot_label(scene, shot): {"keyframe": keyframe.name}
                 for (scene, shot), keyframe in zip(pairs, keyframes)}
        previous = self.project.load_manifest() if scene_limit else {}
        if previous.get("revision") == storyboard.revision:  # Keep keyframes already drawn for later shots.
            shots = {**(previous.get("shots") or {}), **shots}
        manifest = self._manifest(storyboard, refs, shots)
        self.project.save_manifest(manifest)
        return manifest

    def preview_shot(self, storyboard: Storyboard, label: str) -> dict[str, Any]:
        """Render one shot end to end (keyframe, voice, motion, clip) without touching the others."""
        pair = next(((scene, shot) for scene, shot in storyboard.iter_shots() if shot_label(scene, shot) == label), None)
        if pair is None:
            raise KeyError(f"Không có shot {label}")
        refs = self.build_references(storyboard)
        entry = self._render_shots([pair], storyboard, refs)[label]
        manifest = self.project.load_manifest()
        if manifest.get("mode") != "animated":
            manifest = self._manifest(storyboard, refs, {})
        manifest.setdefault("shots", {})[label] = entry
        manifest.setdefault("previews", {})[label] = {"clip": entry["clip"], "revision": storyboard.revision}
        manifest.update(split_references(refs))
        self.project.save_manifest(manifest)
        return manifest
