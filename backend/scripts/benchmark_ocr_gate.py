from __future__ import annotations

import argparse
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from pipeline.ocr import _signatures_are_similar, _subtitle_change_signature


def count_candidates(frame_paths: list[Path], sample_fps: float) -> tuple[int, int]:
    old_previous = None
    old_count = 0
    new_previous = None
    new_count = 0
    last_new_index = -1
    forced_refresh = max(1, math.ceil(sample_fps * 1.5))

    for index, frame_path in enumerate(frame_paths):
        with Image.open(frame_path) as image:
            gray = np.asarray(image.convert("L"), dtype=np.int16)
            signature = _subtitle_change_signature(image)
        old_similar = bool(
            old_previous is not None
            and old_previous.shape == gray.shape
            and float(np.mean(np.abs(old_previous - gray))) < 5.0
        )
        if not old_similar:
            old_count += 1
            old_previous = gray

        new_similar = _signatures_are_similar(new_previous, signature)
        if not new_similar or index - last_new_index >= forced_refresh:
            new_count += 1
            new_previous = signature
            last_new_index = index
    return old_count, new_count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--fps", type=float, default=3.0)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="vidtrans-ocr-gate-") as directory:
        output = Path(directory) / "frame_%06d.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-t",
                str(args.seconds),
                "-i",
                str(args.video),
                "-vf",
                f"fps={args.fps},crop=iw:floor(ih*0.28/2)*2:0:floor(ih*0.68/2)*2",
                "-q:v",
                "2",
                str(output),
            ],
            check=True,
        )
        frames = sorted(Path(directory).glob("frame_*.jpg"))
        old_count, new_count = count_candidates(frames, args.fps)
        reduction = 100.0 * (old_count - new_count) / max(old_count, 1)
        print(
            f"frames={len(frames)} old_candidates={old_count} "
            f"new_candidates={new_count} reduction={reduction:.1f}%"
        )


if __name__ == "__main__":
    main()
