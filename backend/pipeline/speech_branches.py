"""Audio for the extra recognition branches: a BS-RoFormer vocal stem and a DeepFilterNet3 copy.

Both models are optional (requirements-asr-branches.txt). Every function raises when its
model is missing or fails; the caller then skips that branch and keeps the original one.
Outputs are 16 kHz mono PCM WAV, the input Whisper resamples everything to anyway.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# BS-RoFormer checkpoint served by python-audio-separator; overridable for newer releases.
ROFORMER_MODEL = os.environ.get("VIDTRANS_ROFORMER_MODEL", "model_bs_roformer_ep_317_sdr_12.9755.ckpt")
MODEL_DIR = Path(
    os.environ.get("VIDTRANS_SEPARATOR_MODEL_DIR")
    or Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "audio-separator"
)


def cut_clip(ffmpeg: str, video: Path, start: float, end: float, target: Path) -> Path:
    """Lossless PCM copy of the first audio track between start and end, at its own rate."""
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", str(video),
         "-t", f"{max(0.1, end - start):.3f}", "-map", "0:a:0", "-vn", "-sn", "-dn",
         "-c:a", "pcm_s16le", str(target)],
        check=True, capture_output=True, text=True,
    )
    return target


def to_whisper_wav(ffmpeg: str, source: Path, target: Path) -> Path:
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)],
        check=True, capture_output=True, text=True,
    )
    return target


class RoformerSeparator:
    """Vocal stem through python-audio-separator's BS-RoFormer; loaded once, used serially."""

    def __init__(self, model_filename: str = ROFORMER_MODEL, model_dir: Path = MODEL_DIR) -> None:
        self._model_filename = model_filename
        self._model_dir = model_dir
        self._separator: Any = None
        self._lock = threading.Lock()

    def _load(self, output_dir: Path) -> Any:
        if self._separator is None:
            from audio_separator.separator import Separator

            self._model_dir.mkdir(parents=True, exist_ok=True)
            separator = Separator(
                output_dir=str(output_dir),
                output_single_stem="Vocals",
                output_format="WAV",
                model_file_dir=str(self._model_dir),
            )
            separator.load_model(model_filename=self._model_filename)
            self._separator = separator
        self._separator.output_dir = str(output_dir)
        return self._separator

    def separate(self, media_path: Path, output_dir: Path) -> Path:
        """Write the full-rate vocal stem into ``output_dir`` and return its path."""
        output_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            separator = self._load(output_dir)
            outputs = separator.separate(str(media_path))
        for name in outputs or []:
            path = Path(name)
            if not path.is_absolute():
                path = output_dir / path
            if path.is_file():
                return path
        raise RuntimeError("BS-RoFormer produced no vocal stem")


class SpeechEnhancer:
    """DeepFilterNet3 noise suppression; works at the model's own 48 kHz rate."""

    def __init__(self) -> None:
        self._model: Any = None
        self._lock = threading.Lock()

    def enhance(self, media_path: Path, output_path: Path) -> Path:
        from df.enhance import enhance, init_df, load_audio, save_audio

        with self._lock:
            if self._model is None:
                self._model = init_df()
            model, df_state, _ = self._model
            audio, _ = load_audio(str(media_path), sr=df_state.sr())
            enhanced = enhance(model, df_state, audio)
            save_audio(str(output_path), enhanced, df_state.sr())
        return output_path


def build_branch_audio(
    *,
    ffmpeg: str,
    media_path: Path,
    work_dir: Path,
    separator: RoformerSeparator,
    fallback_separate: Callable[[Path, Path], Path] | None,
    enhancer: SpeechEnhancer | None,
    separate: bool = True,
    check_active: Callable[[], None] = lambda: None,
) -> dict[str, Path]:
    """Return {branch name: 16 kHz wav} for the branches that could be produced.

    ``separate`` False skips the vocal stem (noise but no music): the enhancer then
    cleans the original audio directly.
    """
    branch_dir = work_dir
    branch_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {}
    stem: Path | None = None
    if separate:
        stem = _separate(ffmpeg, media_path, branch_dir, separator, fallback_separate, outputs, check_active)
    if enhancer is not None:
        try:
            check_active()
            # Enhance the full-rate audio (stem, else the clip): DeepFilterNet wants 48 kHz detail.
            source = stem or outputs.get("separated") or media_path
            enhanced = enhancer.enhance(source, branch_dir / "enhanced_full.wav")
            outputs["enhanced"] = to_whisper_wav(ffmpeg, enhanced, branch_dir / "enhanced_16k.wav")
        except Exception as exc:
            if type(exc).__name__ == "JobCancelled":
                raise
            logger.warning("DeepFilterNet3 enhancement unavailable; no enhanced branch", exc_info=True)
    if stem is not None:
        shutil.rmtree(branch_dir / "roformer", ignore_errors=True)
    return outputs


def _separate(
    ffmpeg: str,
    media_path: Path,
    branch_dir: Path,
    separator: RoformerSeparator,
    fallback_separate: Callable[[Path, Path], Path] | None,
    outputs: dict[str, Path],
    check_active: Callable[[], None],
) -> Path | None:
    try:
        check_active()
        stem = separator.separate(media_path, branch_dir / "roformer")
        outputs["separated"] = to_whisper_wav(ffmpeg, stem, branch_dir / "separated_16k.wav")
        return stem
    except Exception as exc:
        if type(exc).__name__ == "JobCancelled":
            raise
        logger.warning("BS-RoFormer separation unavailable (%s); trying Demucs", type(exc).__name__, exc_info=True)
    if fallback_separate is not None:
        try:
            check_active()
            outputs["separated"] = fallback_separate(media_path, branch_dir / "separated_16k.wav")
        except Exception as exc:
            if type(exc).__name__ == "JobCancelled":
                raise
            logger.warning("Demucs separation failed; no separated branch", exc_info=True)
    return None
