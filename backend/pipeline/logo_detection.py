"""Find logos/watermarks burned into a video, and where they sit over time.

A logo is the part of the picture that does not move while the scene does: its
edges stay in place frame after frame and the pixels around it keep changing.
Frames are sampled at ~1-2 fps in small grayscale, cut into short windows and
analysed per window, so a logo that jumps between corners (Douyin/TikTok) or only
shows in part of the video yields one placement per stretch of time:

    frames -> windows -> persistent-edge mask -> blobs -> scored candidates
           -> tracks across windows -> one chosen box (or none) per window -> segments

Windows where the whole scene is static cannot tell a logo from the background;
they are "uncertain" and follow their neighbours. No model training is needed;
a learned detector can replace ``detect_candidates`` later behind the same output.
"""
from __future__ import annotations

import math
import subprocess
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

ANALYSIS_WIDTH = 320
WINDOW_SECONDS = 6.0
MIN_WINDOW_FRAMES = 4
MAX_SAMPLED_FRAMES = 360
EDGE_THRESHOLD = 28.0
PERSISTENCE = 0.8
# Above this share of persistent pixels the scene itself is static (slides, fixed camera on a wall).
MAX_STATIC_SHARE = 0.25
MIN_SCENE_CHANGE = 3.0
# Pixels whose value barely changes across the window (logo fill, solid borders).
STATIC_STD = 8.0


@dataclass(frozen=True)
class Box:
    """Normalised rectangle: every value is a share of the frame width/height."""

    x: float
    y: float
    w: float
    h: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    @property
    def area(self) -> float:
        return self.w * self.h

    def iou(self, other: "Box") -> float:
        left, top = max(self.x, other.x), max(self.y, other.y)
        right = min(self.x + self.w, other.x + other.w)
        bottom = min(self.y + self.h, other.y + other.h)
        inter = max(0.0, right - left) * max(0.0, bottom - top)
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0


@dataclass(frozen=True)
class Candidate:
    box: Box
    confidence: float
    # Which pixels inside ``box`` are the logo's strokes (analysis resolution), for inpainting.
    stencil: np.ndarray | None = field(default=None, compare=False, repr=False)


@dataclass
class Window:
    start: float
    end: float
    valid: bool
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class Segment:
    """A stretch of the video and the logo box in it, or None where no logo shows."""

    start: float
    end: float
    box: Box | None
    confidence: float
    # Index into ``Detection.stencils``: the mask used to inpaint this logo.
    track: int | None = None
    # A moving logo: (t, centre x, centre y) shares of the frame; the renderer interpolates.
    keyframes: list[tuple[float, float, float]] | None = None


@dataclass
class Detection:
    # found: at least one stretch has a logo; not_found: the scene was readable and had none;
    # uncertain: the scene never moved enough, or only weak candidates were seen.
    status: str
    confidence: float
    segments: list[Segment]
    candidates: list[dict]
    windows: int
    valid_windows: int
    method: str = "persistence"  # "persistence" | "template"
    stencils: dict[int, np.ndarray] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        return {
            "status": self.status, "confidence": self.confidence, "method": self.method,
            "segments": [asdict(segment) for segment in self.segments], "candidates": self.candidates,
            "windows": self.windows, "valid_windows": self.valid_windows,
        }


# ------------------------------------------------------------------ frame sampling

def analysis_size(width: int, height: int) -> tuple[int, int]:
    scaled_height = max(2, int(round(height * ANALYSIS_WIDTH / max(1, width) / 2)) * 2)
    return ANALYSIS_WIDTH, scaled_height


def sample_frames(
    ffmpeg: str,
    video_path: Path,
    *,
    width: int,
    height: int,
    duration: float,
    sample_fps: float = 1.5,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (timestamps, gray frames[n, h, w]) sampled evenly over the video."""
    fps = min(sample_fps, MAX_SAMPLED_FRAMES / max(duration, 1.0))
    out_w, out_h = analysis_size(width, height)
    result = run(
        [ffmpeg, "-v", "error", "-i", str(video_path), "-an", "-sn",
         "-vf", f"fps={fps:.4f}:round=up,scale={out_w}:{out_h},format=gray",
         "-f", "rawvideo", "pipe:1"],
        capture_output=True, check=True,
    )
    frame_bytes = out_w * out_h
    count = len(result.stdout) // frame_bytes
    frames = np.frombuffer(result.stdout[: count * frame_bytes], dtype=np.uint8).reshape(count, out_h, out_w)
    return np.arange(count, dtype=np.float64) / fps, frames


# ------------------------------------------------------------------ per-window analysis

def _edges(frames: np.ndarray) -> np.ndarray:
    data = frames.astype(np.float32)
    gx = np.zeros_like(data)
    gy = np.zeros_like(data)
    gx[:, :, 1:] = np.abs(np.diff(data, axis=2))
    gy[:, 1:, :] = np.abs(np.diff(data, axis=1))
    return np.maximum(gx, gy) > EDGE_THRESHOLD


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(radius):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Bounding boxes (x0, y0, x1, y1 exclusive) of 4-connected blobs."""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    boxes = []
    for start_y, start_x in zip(*np.nonzero(mask)):
        if seen[start_y, start_x]:
            continue
        seen[start_y, start_x] = True
        queue = deque([(start_y, start_x)])
        x0 = x1 = start_x
        y0 = y1 = start_y
        while queue:
            y, x = queue.popleft()
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        boxes.append((int(x0), int(y0), int(x1) + 1, int(y1) + 1))
    return boxes


def _location_prior(box: Box) -> float:
    """Logos live in corners, sometimes along an edge, almost never in the middle."""
    cx, cy = box.center
    near_x = cx < 0.3 or cx > 0.7
    near_y = cy < 0.3 or cy > 0.7
    if near_x and near_y:
        return 1.0
    if near_x or near_y:
        return 0.6
    return 0.0


def _grow_into_static(mask: np.ndarray, temporal_std: np.ndarray, x0: int, y0: int, x1: int, y1: int,
                      ) -> tuple[tuple[int, int, int, int], np.ndarray]:
    """The logo body: its persistent edges plus the unchanging pixels connected to them.

    A solid border or fill has no edges of its own once the scene behind it changes
    colour, so edges alone outline only the logo's inner details; inpainting that
    would leave the border and refill the hole with its colour.
    """
    height, width = mask.shape
    reach = max(2, min(x1 - x0, y1 - y0) // 2)
    gx0, gy0, gx1, gy1 = max(0, x0 - reach), max(0, y0 - reach), min(width, x1 + reach), min(height, y1 + reach)
    region = np.zeros((gy1 - gy0, gx1 - gx0), dtype=bool)
    region[y0 - gy0:y1 - gy0, x0 - gx0:x1 - gx0] = mask[y0:y1, x0:x1]
    allowed = (temporal_std[gy0:gy1, gx0:gx1] < STATIC_STD) | region
    for _ in range(reach):
        grown = _dilate(region, 1) & allowed
        if np.array_equal(grown, region):
            break
        region = grown
    ys, xs = np.nonzero(region)
    bx0, by0, bx1, by1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    return (gx0 + bx0, gy0 + by0, gx0 + bx1, gy0 + by1), region[by0:by1, bx0:bx1]


def detect_candidates(frames: np.ndarray) -> tuple[bool, list[Candidate]]:
    """Analyse one window of frames; returns (scene readable, candidates)."""
    if len(frames) < 2:
        return False, []
    data = frames.astype(np.float32)
    scene_change = float(np.mean(np.abs(np.diff(data, axis=0))))
    if scene_change < MIN_SCENE_CHANGE:
        return False, []
    height, width = frames.shape[1:]
    persistence = _edges(frames).mean(axis=0)
    temporal_std = data.std(axis=0)
    mask = (persistence >= PERSISTENCE) & (temporal_std < 18.0)
    # Ignore the outermost pixels: letterbox bars and encoder borders are not logos.
    mask[:2, :] = mask[-2:, :] = False
    mask[:, :2] = mask[:, -2:] = False
    if mask.mean() > MAX_STATIC_SHARE:
        return False, []
    radius = max(2, width // 80)
    candidates = []
    for x0, y0, x1, y1 in _components(_dilate(mask, radius)):
        # Undo the dilation, then judge the blob by its own pixels.
        x0, y0 = min(x0 + radius, x1 - 1), min(y0 + radius, y1 - 1)
        x1, y1 = max(x1 - radius, x0 + 1), max(y1 - radius, y0 + 1)
        bw, bh = x1 - x0, y1 - y0
        box = Box(x0 / width, y0 / height, bw / width, bh / height)
        if not (0.001 <= box.area <= 0.08) or box.w < 0.02 or box.w > 0.5 or box.h > 0.4:
            continue
        if not 0.2 <= bw / max(bh, 1) <= 12:
            continue
        inside = mask[y0:y1, x0:x1]
        density = float(inside.mean())
        location = _location_prior(box)
        if density < 0.04 or location == 0.0:
            continue
        pad = max(2, radius * 2)
        ring = np.zeros_like(mask)
        ring[max(0, y0 - pad):min(height, y1 + pad), max(0, x0 - pad):min(width, x1 + pad)] = True
        ring[y0:y1, x0:x1] = False
        ring_change = float(temporal_std[ring].mean()) if ring.any() else 0.0
        inside_change = float(temporal_std[y0:y1, x0:x1][inside].mean())
        change = min(1.0, max(0.0, (ring_change - inside_change) / 10.0))
        steadiness = min(1.0, max(0.0, (float(persistence[y0:y1, x0:x1][inside].mean()) - PERSISTENCE) / 0.2))
        confidence = 0.4 * steadiness + 0.35 * change + 0.15 * location + 0.1 * min(1.0, density * 4)
        (bx0, by0, bx1, by1), stencil = _grow_into_static(mask, temporal_std, x0, y0, x1, y1)
        body = Box(bx0 / width, by0 / height, (bx1 - bx0) / width, (by1 - by0) / height)
        candidates.append(Candidate(body, round(confidence, 3), stencil))
    return True, sorted(candidates, key=lambda item: -item.confidence)


# ------------------------------------------------------------------ whole-video decision

def _corner_point(position: str) -> tuple[float, float]:
    return (0.0 if position.endswith("left") else 1.0, 0.0 if position.startswith("top") else 1.0)


def _choose(candidates: Sequence[Candidate], strategy: str, default_position: str) -> Candidate:
    if strategy == "largest":
        return max(candidates, key=lambda item: (item.box.area, item.confidence))
    if strategy == "most-confident":
        return max(candidates, key=lambda item: item.confidence)
    corner = _corner_point(default_position)
    return min(candidates, key=lambda item: (math.dist(item.box.center, corner), -item.confidence))


def _median_box(boxes: Sequence[Box]) -> Box:
    values = np.array([[b.x, b.y, b.w, b.h] for b in boxes])
    x, y, w, h = np.median(values, axis=0)
    return Box(round(float(x), 4), round(float(y), 4), round(float(w), 4), round(float(h), 4))


def analyse_windows(
    timestamps: np.ndarray,
    frames: np.ndarray,
    duration: float,
    *,
    window_seconds: float = WINDOW_SECONDS,
) -> list[Window]:
    if len(frames) == 0:
        return []
    count = max(1, int(round(duration / window_seconds)))
    edges = np.linspace(0.0, duration, count + 1)
    windows = []
    for start, end in zip(edges[:-1], edges[1:]):
        selected = (timestamps >= start) & (timestamps < end)
        if end == edges[-1]:
            selected |= timestamps >= end
        window_frames = frames[selected]
        if len(window_frames) < MIN_WINDOW_FRAMES:
            windows.append(Window(float(start), float(end), False))
            continue
        valid, candidates = detect_candidates(window_frames)
        windows.append(Window(float(start), float(end), valid, candidates))
    return windows


def decide(
    windows: Sequence[Window],
    duration: float,
    *,
    strategy: str = "nearest-default",
    default_position: str = "top-right",
    min_confidence: float = 0.55,
) -> Detection:
    """Turn per-window candidates into stable segments covering the whole video."""
    tracks: list[dict] = []
    for index, window in enumerate(windows):
        for candidate in window.candidates:
            track = next((t for t in tracks if _median_box(t["boxes"]).iou(candidate.box) > 0.3), None)
            if track is None:
                track = {"boxes": [], "confidences": [], "windows": [], "stencil": None, "best": -1.0}
                tracks.append(track)
            if candidate.stencil is not None and candidate.confidence > track["best"]:
                track["stencil"], track["best"] = candidate.stencil, candidate.confidence
            track["boxes"].append(candidate.box)
            track["confidences"].append(candidate.confidence)
            track["windows"].append(index)
    valid_count = sum(1 for window in windows if window.valid)
    for track in tracks:
        track["box"] = _median_box(track["boxes"])
        coverage = len(set(track["windows"])) / max(1, valid_count)
        # A logo that is there most of the time is more believable than a one-off static object.
        track["confidence"] = round(float(np.mean(track["confidences"])) * (0.75 + 0.25 * coverage), 3)
        track["coverage"] = round(coverage, 3)

    chosen: list[int | None] = []  # track index per window; -1 marks "unknown, follow neighbours"
    for index, window in enumerate(windows):
        if not window.valid:
            chosen.append(-1)
            continue
        options = [
            (track_index, Candidate(track["box"], track["confidence"]))
            for track_index, track in enumerate(tracks)
            if index in track["windows"] and track["confidence"] >= min_confidence
        ]
        if not options:
            chosen.append(None)
            continue
        pick = _choose([option for _, option in options], strategy, default_position)
        chosen.append(next(track_index for track_index, option in options if option is pick))
    # Uncertain windows take the previous decision, or the next one at the start of the video.
    for index in range(len(chosen)):
        if chosen[index] == -1 and index > 0:
            chosen[index] = chosen[index - 1]
    for index in reversed(range(len(chosen))):
        if chosen[index] == -1 and index + 1 < len(chosen):
            chosen[index] = chosen[index + 1]
    chosen = [None if value == -1 else value for value in chosen]

    segments: list[Segment] = []
    for window, track_index in zip(windows, chosen):
        box = tracks[track_index]["box"] if track_index is not None else None
        confidence = tracks[track_index]["confidence"] if track_index is not None else 0.0
        if segments and segments[-1].box == box:
            segments[-1].end = window.end
        else:
            segments.append(Segment(window.start, window.end, box, confidence, track_index))
    if segments:
        segments[0].start, segments[-1].end = 0.0, duration

    found = [segment for segment in segments if segment.box is not None]
    weak = any(t["confidence"] >= min_confidence - 0.2 for t in tracks)
    if found:
        status = "found"
    elif valid_count == 0 or weak:
        status = "uncertain"
    else:
        status = "not_found"
    candidates = [
        {"box": asdict(track["box"]), "confidence": track["confidence"], "coverage": track["coverage"],
         "start": windows[min(track["windows"])].start, "end": windows[max(track["windows"])].end}
        for track in sorted(tracks, key=lambda t: -t["confidence"])[:5]
    ]
    return Detection(
        status=status,
        confidence=max((segment.confidence for segment in found), default=0.0),
        segments=segments or [Segment(0.0, duration, None, 0.0)],
        candidates=candidates,
        windows=len(windows),
        valid_windows=valid_count,
        stencils={
            segment.track: tracks[segment.track]["stencil"] for segment in found
            if tracks[segment.track]["stencil"] is not None
        },
    )


def detect_logo(
    ffmpeg: str,
    video_path: Path,
    *,
    width: int,
    height: int,
    duration: float,
    strategy: str = "nearest-default",
    default_position: str = "top-right",
    min_confidence: float = 0.55,
    sample_fps: float = 1.5,
) -> Detection:
    timestamps, frames = sample_frames(
        ffmpeg, video_path, width=width, height=height, duration=duration, sample_fps=sample_fps,
    )
    windows = analyse_windows(timestamps, frames, duration)
    return decide(windows, duration, strategy=strategy, default_position=default_position,
                  min_confidence=min_confidence)
