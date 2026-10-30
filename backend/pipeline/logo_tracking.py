"""Follow a known logo (the user's template image) through a video, even while it moves.

Persistent-edge detection (``logo_detection``) only sees logos that stand still for
seconds. When the user uploads a sample of the logo to replace, it is searched for
directly: template matching on blurred gradient images (robust to semi-transparent
watermarks, to the scene behind them and to 1-2 px misalignment), first across scales on a few spread-out
frames to learn its size in this video, then frame by frame at ~4 fps. The path is
smoothed and reduced to a few keyframes, so the renderer can move the new logo with
piecewise-linear x(t)/y(t) instead of one overlay per sample.

Needs OpenCV (shipped with PaddleOCR); ``available()`` says whether it is present.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Sequence

import numpy as np

from pipeline.logo_detection import Box, Detection, Segment

ANALYSIS_WIDTH = 640
SAMPLE_FPS = 4.0
SCALE_PROBE_FRAMES = 12
# Template width as a share of the frame width.
SCALES = tuple(float(value) for value in np.geomspace(0.03, 0.5, 20))
MATCH_THRESHOLD = 0.75
# How far the best match must stand out from the rest of the frame (standard deviations).
# Tiny or edge-poor templates fit busy scenes anywhere with a decent score but no clear peak.
MIN_PEAK_Z = 7.0
# A very close match needs less of a peak (busy textures flatten every peak).
STRONG_MATCH, STRONG_MATCH_PEAK_Z = 0.83, 5.0
# Keyframe reduction tolerance, as a share of the frame width.
PATH_TOLERANCE = 0.004
MAX_KEYFRAMES = 120
# Smallest template searched, in analysis pixels: below this anything matches anything.
MIN_TEMPLATE_W, MIN_TEMPLATE_H = 28, 14
# A move larger than this between two samples (share of the frame) is a jump, not motion.
MAX_STEP = 0.12
MIN_RUN_SAMPLES = 3


def available() -> bool:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass(frozen=True)
class Template:
    gray: np.ndarray   # template luminance composited on mid-gray, uint8
    alpha: np.ndarray  # bool: pixels that belong to the logo

    @property
    def aspect(self) -> float:
        return self.gray.shape[0] / self.gray.shape[1]


def load_template(path: Path) -> Template:
    from PIL import Image

    with Image.open(path) as image:
        rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = rgba[:, :, 3] / 255.0
    luminance = rgba[:, :, :3] @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    gray = luminance * alpha + 128.0 * (1.0 - alpha)
    return Template(gray.clip(0, 255).astype(np.uint8), alpha > 0.12)


def _gradient(image: np.ndarray) -> np.ndarray:
    import cv2

    gx = cv2.Sobel(image, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(image, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.GaussianBlur(cv2.magnitude(gx, gy), (0, 0), 1.5)


class Matcher:
    """Template gradients prepared once per scale for a given frame width."""

    def __init__(self, template: Template, frame_w: int, frame_h: int) -> None:
        import cv2

        self.frame_w, self.frame_h = frame_w, frame_h
        self.scaled: dict[float, np.ndarray] = {}
        for scale in SCALES:
            width = int(round(frame_w * scale))
            height = int(round(width * template.aspect))
            if width < MIN_TEMPLATE_W or height < MIN_TEMPLATE_H or width >= frame_w or height >= frame_h:
                continue
            resized = cv2.resize(template.gray, (width, height), interpolation=cv2.INTER_AREA)
            self.scaled[scale] = _gradient(resized)

    def match(self, frame_gradient: np.ndarray, scales: Sequence[float]) -> tuple[float, float, Box | None]:
        """Best (score, peak z-score, box) of the template in one frame among ``scales``."""
        import cv2

        best = (-1.0, 0.0, None)
        for scale in scales:
            template = self.scaled.get(scale)
            if template is None:
                continue
            result = cv2.matchTemplate(frame_gradient, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, (x, y) = cv2.minMaxLoc(result)
            mean, std = cv2.meanStdDev(result)
            peak = (score - float(mean[0][0])) / (float(std[0][0]) + 1e-6)
            if score > best[0]:
                height, width = template.shape
                box = Box(x / self.frame_w, y / self.frame_h, width / self.frame_w, height / self.frame_h)
                best = (float(score), float(peak), box)
        return best


def _frames(ffmpeg: str, video_path: Path, fps: float, width: int, height: int,
            popen: Callable[..., subprocess.Popen] = subprocess.Popen) -> Iterator[tuple[float, np.ndarray]]:
    process = popen(
        [ffmpeg, "-v", "error", "-i", str(video_path), "-an", "-sn",
         # round=up: sample n is the last source frame at or before n/fps, so its timestamp is honest
         # (the default keeps the last frame of each slot, ~half a slot late, and the path lags).
         "-vf", f"fps={fps:.5f}:round=up,scale={width}:{height},format=gray", "-f", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    size = width * height
    index = 0
    try:
        while True:
            chunk = process.stdout.read(size)
            if len(chunk) < size:
                break
            yield index / fps, np.frombuffer(chunk, dtype=np.uint8).reshape(height, width)
            index += 1
    finally:
        process.kill()
        process.wait()


def is_hit(score: float, peak: float, threshold: float = MATCH_THRESHOLD) -> bool:
    return (score >= threshold and peak >= MIN_PEAK_Z) or (score >= STRONG_MATCH and peak >= STRONG_MATCH_PEAK_Z)


def simplify_path(points: Sequence[tuple[float, float, float]], tolerance: float) -> list[tuple[float, float, float]]:
    """Keep the keyframes needed so linear interpolation stays within ``tolerance`` (x, y share)."""
    if len(points) <= 2:
        return list(points)
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        t0, x0, y0 = points[first]
        t1, x1, y1 = points[last]
        worst, worst_index = 0.0, -1
        for index in range(first + 1, last):
            t, x, y = points[index]
            share = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            deviation = max(abs(x - (x0 + (x1 - x0) * share)), abs(y - (y0 + (y1 - y0) * share)))
            if deviation > worst:
                worst, worst_index = deviation, index
        if worst > tolerance:
            keep.add(worst_index)
            stack += [(first, worst_index), (worst_index, last)]
    return [points[index] for index in sorted(keep)]


def build_segments(samples: Sequence[tuple[float, float, Box | None]], duration: float,
                   sample_interval: float) -> list[Segment]:
    """Group per-sample matches (t, score, box|None) into present/absent stretches with keyframes."""
    runs: list[list[tuple[float, float, Box]]] = []
    for t, score, box in samples:
        if box is None:
            continue
        previous = runs[-1][-1] if runs else None
        if previous and t - previous[0] <= sample_interval * 2.01 and max(
                abs(box.center[0] - previous[2].center[0]), abs(box.center[1] - previous[2].center[1])) <= MAX_STEP:
            runs[-1].append((t, score, box))
        else:
            # Gap, or the logo jumped elsewhere (Douyin-style corner hops): start a new stretch.
            runs.append([(t, score, box)])
    # Brief hits are more likely coincidences than a logo.
    runs = [run for run in runs if len(run) >= MIN_RUN_SAMPLES]
    segments: list[Segment] = []
    cursor = 0.0
    slack = sample_interval * 2.01
    for index, run in enumerate(runs):
        start = run[0][0] - sample_interval / 2
        # Close gaps shorter than the sampling can resolve, so the new logo never flashes elsewhere.
        start = cursor if start - cursor <= slack else start
        following = runs[index + 1][0][0] - sample_interval / 2 if index + 1 < len(runs) else duration
        end = run[-1][0] + sample_interval / 2
        end = following if following - end <= slack else end
        if start > cursor:
            segments.append(Segment(cursor, start, None, 0.0))
        centers = np.array([box.center for _, _, box in run])
        if len(run) >= 3:  # 3-sample median removes single-frame jumps.
            padded = np.vstack([centers[:1], centers, centers[-1:]])
            centers = np.median(np.stack([padded[:-2], padded[1:-1], padded[2:]]), axis=0)
        width = float(np.median([box.w for _, _, box in run]))
        height = float(np.median([box.h for _, _, box in run]))
        points = [(t, float(cx), float(cy)) for (t, _, _), (cx, cy) in zip(run, centers)]
        keyframes = simplify_path(points, PATH_TOLERANCE)
        if len(keyframes) > MAX_KEYFRAMES:
            step = len(keyframes) / MAX_KEYFRAMES
            keyframes = [keyframes[int(i * step)] for i in range(MAX_KEYFRAMES - 1)] + [keyframes[-1]]
        cx, cy = float(np.median(centers[:, 0])), float(np.median(centers[:, 1]))
        box = Box(round(cx - width / 2, 4), round(cy - height / 2, 4), round(width, 4), round(height, 4))
        moving = len(keyframes) > 2 or (
            len(keyframes) == 2 and max(abs(keyframes[0][1] - keyframes[1][1]),
                                        abs(keyframes[0][2] - keyframes[1][2])) > PATH_TOLERANCE)
        confidence = round(float(np.mean([score for _, score, _ in run])), 3)
        segments.append(Segment(start, end, box, confidence, track=0,
                                keyframes=[(round(t, 3), round(x, 4), round(y, 4)) for t, x, y in keyframes]
                                if moving else None))
        cursor = end
    if cursor < duration:
        segments.append(Segment(cursor, duration, None, 0.0))
    return segments


def track_template(
    ffmpeg: str,
    video_path: Path,
    template_path: Path,
    *,
    width: int,
    height: int,
    duration: float,
    sample_fps: float = SAMPLE_FPS,
    threshold: float = MATCH_THRESHOLD,
    progress: Callable[[float], None] | None = None,
    frames: Callable[..., Iterator[tuple[float, np.ndarray]]] = _frames,
) -> Detection:
    template = load_template(template_path)
    frame_w = min(ANALYSIS_WIDTH, width)
    frame_h = max(2, int(round(height * frame_w / max(1, width) / 2)) * 2)
    matcher = Matcher(template, frame_w, frame_h)
    if not matcher.scaled:
        return Detection("uncertain", 0.0, [Segment(0.0, duration, None, 0.0)], [], 0, 0)

    # 1) Learn the logo's size in this video from a few frames spread over it.
    probe_fps = SCALE_PROBE_FRAMES / max(duration, 1.0)
    per_scale: dict[float, list[float]] = {}
    for _, frame in frames(ffmpeg, video_path, probe_fps, frame_w, frame_h):
        gradient = _gradient(frame)
        for scale in matcher.scaled:
            _, peak, _ = matcher.match(gradient, [scale])
            per_scale.setdefault(scale, []).append(peak)
    if not per_scale:
        return Detection("uncertain", 0.0, [Segment(0.0, duration, None, 0.0)], [], 0, 0)
    # The logo may be absent from some probes; rank scales by their better half of frames.
    ranked = sorted(per_scale, key=lambda s: -float(np.mean(sorted(per_scale[s])[len(per_scale[s]) // 2:])))
    best_scale = ranked[0]
    ordered = sorted(matcher.scaled)
    position = ordered.index(best_scale)
    scales = ordered[max(0, position - 1):position + 2]

    # 2) Follow it frame by frame at that size.
    fps = min(sample_fps, 1200 / max(duration, 1.0))
    samples: list[tuple[float, float, Box | None]] = []
    expected = max(1, int(duration * fps))
    for index, (t, frame) in enumerate(frames(ffmpeg, video_path, fps, frame_w, frame_h)):
        score, peak, box = matcher.match(_gradient(frame), scales)
        samples.append((t, score, box if is_hit(score, peak, threshold) else None))
        if progress and index % 10 == 0:
            progress(min(1.0, index / expected))
    segments = build_segments(samples, duration, 1.0 / fps)
    found = [segment for segment in segments if segment.box is not None]
    best_score = max((score for _, score, _ in samples), default=0.0)
    if found:
        status = "found"
    elif best_score >= threshold - 0.05:
        status = "uncertain"
    else:
        status = "not_found"
    hits = sum(1 for _, _, box in samples if box is not None)
    candidates = [{"box": segment.box.__dict__, "confidence": segment.confidence,
                   "coverage": round(hits / max(1, len(samples)), 3), "start": segment.start, "end": segment.end,
                   "moving": bool(segment.keyframes)} for segment in found[:5]]
    return Detection(
        status=status,
        confidence=max((segment.confidence for segment in found), default=0.0),
        segments=segments or [Segment(0.0, duration, None, 0.0)],
        candidates=candidates,
        windows=len(samples),
        valid_windows=len(samples),
        method="template",
        stencils={0: template.alpha} if found else {},
    )
