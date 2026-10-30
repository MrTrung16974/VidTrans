"""Remove an old logo by reconstructing the background under its strokes.

``delogo`` blurs a whole rectangle; this repairs only the logo's own pixels from
their surroundings (OpenCV Telea inpainting), so the rest of the corner stays
sharp. Frames stream through Python between two FFmpeg processes:

    ffmpeg decode (CFR, BGR) -> inpaint the masked pixels -> ffmpeg encode + new logo overlay

The mask is the detection stencil (persistent edges, or the user's template alpha)
closed and hole-filled so solid letters are removed, not just their outlines, then
positioned per frame (moving logos follow their keyframes).
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from pipeline.logo_overlay import Placement, build_decode_command, build_raw_input_command

INPAINT_RADIUS = 5


def available() -> bool:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def solid_mask(stencil: np.ndarray | None, width: int, height: int, grow: int) -> np.ndarray:
    """Stencil resized to the logo box, gaps between strokes closed and enclosed holes filled."""
    import cv2

    if stencil is None or not stencil.any():
        mask = np.full((height, width), 255, dtype=np.uint8)
    else:
        mask = cv2.resize(stencil.astype(np.uint8) * 255, (width, height), interpolation=cv2.INTER_NEAREST)
        close = max(3, int(min(width, height) * 0.08) | 1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((close, close), np.uint8))
        # Anything the border flood cannot reach is enclosed by the logo: fill it.
        padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
        outside = padded.copy()
        cv2.floodFill(outside, None, (0, 0), 255)
        mask = (padded | cv2.bitwise_not(outside))[1:-1, 1:-1]
    if grow > 0:
        mask = cv2.dilate(mask, np.ones((grow * 2 + 1, grow * 2 + 1), np.uint8))
    return mask


class FrameRepairer:
    """Inpaints the old logo(s) active at a timestamp on one BGR frame."""

    def __init__(self, placements: Sequence[Placement], stencils: dict, width: int, height: int) -> None:
        self.covers = [p for p in placements if p.cover_box is not None]
        self.stencils = stencils
        self.width, self.height = width, height
        # Covers stroke anti-aliasing plus the tracker's error (about one analysis pixel).
        self.grow = max(3, int(min(width, height) * 0.01))
        self._masks: dict[tuple, np.ndarray] = {}

    def _mask(self, placement: Placement, box_w: int, box_h: int) -> np.ndarray:
        key = (placement.cover_track, box_w, box_h)
        if key not in self._masks:
            self._masks[key] = solid_mask(self.stencils.get(placement.cover_track), box_w, box_h, self.grow)
        return self._masks[key]

    def active(self, t: float) -> list[Placement]:
        last = self.covers[-1] if self.covers else None
        return [p for p in self.covers if p.start <= t < p.end or (p is last and t >= p.start)]

    def repair(self, frame: np.ndarray, t: float) -> bool:
        import cv2

        changed = False
        for placement in self.active(t):
            x, y, w, h = placement.cover_box
            cx, cy = x + w / 2, y + h / 2
            if placement.cover_keyframes:
                times = [key[0] for key in placement.cover_keyframes]
                cx = float(np.interp(t, times, [key[1] for key in placement.cover_keyframes]))
                cy = float(np.interp(t, times, [key[2] for key in placement.cover_keyframes]))
            box_w, box_h = max(2, int(round(w * self.width))), max(2, int(round(h * self.height)))
            mask = self._mask(placement, box_w, box_h)
            pad = self.grow + INPAINT_RADIUS * 2
            left, top = int(round(cx * self.width - box_w / 2)), int(round(cy * self.height - box_h / 2))
            x0, y0 = max(0, left - pad), max(0, top - pad)
            x1, y1 = min(self.width, left + box_w + pad), min(self.height, top + box_h + pad)
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            roi_mask = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
            # Paste the box mask into the ROI, clipped where the box leaves the frame.
            mx0, my0 = max(0, x0 - left), max(0, y0 - top)
            mx1, my1 = min(box_w, x1 - left), min(box_h, y1 - top)
            if mx1 <= mx0 or my1 <= my0:
                continue
            roi_mask[my0 + top - y0:my1 + top - y0, mx0 + left - x0:mx1 + left - x0] = mask[my0:my1, mx0:mx1]
            frame[y0:y1, x0:x1] = cv2.inpaint(frame[y0:y1, x0:x1], roi_mask, INPAINT_RADIUS, cv2.INPAINT_TELEA)
            changed = True
        return changed


def render_inpainted(
    ffmpeg: str,
    video_path: Path,
    logo_path: Path,
    output_path: Path,
    placements: Sequence[Placement],
    stencils: dict,
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
    opacity: float,
    copy_audio: bool,
    on_progress: Callable[[float], None],
) -> None:
    """Decode, repair and re-encode; ``on_progress`` may raise to cancel (both processes are killed)."""
    repairer = FrameRepairer(placements, stencils, width, height)
    frame_size = width * height * 3
    with tempfile.TemporaryFile() as decode_log, tempfile.TemporaryFile() as encode_log:
        decoder = subprocess.Popen(build_decode_command(ffmpeg, video_path, fps),
                                   stdout=subprocess.PIPE, stderr=decode_log)
        encoder = subprocess.Popen(
            build_raw_input_command(ffmpeg, video_path, logo_path, output_path, placements, width=width,
                                    height=height, fps=fps, opacity=opacity, copy_audio=copy_audio),
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=encode_log,
        )

        def failure(process: subprocess.Popen, log) -> subprocess.CalledProcessError:
            log.seek(0)
            return subprocess.CalledProcessError(process.returncode or 1, process.args,
                                                 stderr=log.read().decode("utf-8", "replace")[-2000:])

        try:
            index = 0
            while True:
                chunk = decoder.stdout.read(frame_size)
                if len(chunk) < frame_size:
                    break
                t = index / fps
                if repairer.active(t):
                    frame = np.frombuffer(chunk, dtype=np.uint8).reshape(height, width, 3).copy()
                    if repairer.repair(frame, t):
                        chunk = frame.tobytes()
                try:
                    encoder.stdin.write(chunk)
                except BrokenPipeError:
                    encoder.wait()
                    raise failure(encoder, encode_log) from None
                index += 1
                if index % 25 == 0 and duration > 0:
                    on_progress(min(1.0, t / duration))
            encoder.stdin.close()
            if decoder.wait() != 0:
                raise failure(decoder, decode_log)
            if encoder.wait() != 0:
                raise failure(encoder, encode_log)
            if index == 0:
                raise subprocess.CalledProcessError(1, decoder.args, stderr="no frames decoded")
        except BaseException:
            for process in (decoder, encoder):
                process.kill()
                process.wait()
            raise
