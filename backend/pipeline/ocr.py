from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
import threading
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median
from typing import Any, Callable, Protocol, Sequence


logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class OCRConfig:
    """Settings for reading burned-in subtitles from the lower video region."""

    sample_fps: float = 5.0
    roi_top: float = 0.68
    roi_bottom: float = 0.96
    min_confidence: float = 0.55
    text_similarity: float = 0.82
    blank_tolerance_frames: int = 1
    min_stable_frames: int = 2
    upscale: float = 2.0

    def validate(self) -> None:
        if not 0 < self.sample_fps <= 30:
            raise ValueError("ocr sample_fps must be in (0, 30]")
        if not 0 <= self.roi_top < self.roi_bottom <= 1:
            raise ValueError("OCR ROI must satisfy 0 <= top < bottom <= 1")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("ocr min_confidence must be in [0, 1]")
        if not 0 <= self.text_similarity <= 1:
            raise ValueError("ocr text_similarity must be in [0, 1]")
        if self.blank_tolerance_frames < 0:
            raise ValueError("ocr blank_tolerance_frames cannot be negative")
        if self.min_stable_frames < 1:
            raise ValueError("ocr min_stable_frames must be at least 1")
        if self.upscale < 1:
            raise ValueError("ocr upscale must be at least 1")


@dataclass(frozen=True)
class OCRLine:
    text: str
    confidence: float
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class FrameObservation:
    timestamp: float
    text: str
    confidence: float
    bbox: tuple[float, float, float, float] | None = None


@dataclass
class SubtitleCue:
    start: float
    end: float
    text: str
    confidence: float
    samples: int
    variants: dict[str, int] = field(default_factory=dict)
    bbox: tuple[float, float, float, float] | None = None
    position_confidence: float = 0.0

    def as_segment(self) -> dict[str, Any]:
        segment: dict[str, Any] = {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "ocr_confidence": round(self.confidence, 4),
            "ocr_samples": self.samples,
            "ocr_variants": dict(self.variants),
            "source_method": "ocr",
        }
        if self.bbox is not None:
            x1, y1, x2, y2 = self.bbox
            segment.update(
                {
                    "source_bbox": {
                        "x": round(x1, 6),
                        "y": round(y1, 6),
                        "width": round(x2 - x1, 6),
                        "height": round(y2 - y1, 6),
                    },
                    "source_font_height": round((y2 - y1) / max(self.text.count("\n") + 1, 1), 6),
                    "position_confidence": round(self.position_confidence, 4),
                    "position_source": "ocr",
                }
            )
        return segment


class OCRReader(Protocol):
    def read(self, image_path: Path) -> list[OCRLine]: ...


def normalize_zh_text(value: str) -> str:
    """Normalize representation without converting simplified/traditional Chinese."""

    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("|", "").replace("丨", "")
    return _SPACE_RE.sub("", value).strip()


def contains_cjk(value: str) -> bool:
    return bool(_CJK_RE.search(value))


def text_similarity(left: str, right: str) -> float:
    left_norm = normalize_zh_text(left)
    right_norm = normalize_zh_text(right)
    if not left_norm or not right_norm:
        return 1.0 if left_norm == right_norm else 0.0
    return SequenceMatcher(None, left_norm, right_norm, autojunk=False).ratio()


def annotate_ocr_segments_with_asr(
    ocr_segments: list[dict[str, Any]],
    asr_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach ASR evidence without allowing it to overwrite text visible on screen."""

    for cue in ocr_segments:
        overlapping = [
            segment
            for segment in asr_segments
            if min(float(cue["end"]), float(segment["end"]))
            - max(float(cue["start"]), float(segment["start"]))
            > 0
        ]
        asr_text = normalize_zh_text("".join(segment["text"] for segment in overlapping))
        source_text = normalize_zh_text(cue["text"])
        similarity = text_similarity(source_text, asr_text) if asr_text else 0.0
        cue["asr_text"] = asr_text
        cue["asr_similarity"] = round(similarity, 4)
        cue["needs_review"] = bool(
            float(cue.get("ocr_confidence", 0.0)) < 0.8
            or (asr_text and similarity < 0.45)
        )
    return ocr_segments


def select_subtitle_text(lines: Sequence[OCRLine], min_confidence: float) -> tuple[str, float]:
    """Select Chinese text lines in visual reading order from an OCR result."""

    candidates = [
        line
        for line in lines
        if line.confidence >= min_confidence and contains_cjk(line.text)
    ]
    if not candidates:
        return "", 0.0

    candidates.sort(
        key=lambda line: (
            line.bbox[1] if line.bbox else math.inf,
            line.bbox[0] if line.bbox else math.inf,
        )
    )
    texts = [normalize_zh_text(line.text) for line in candidates]
    texts = [text for text in texts if text]
    if not texts:
        return "", 0.0
    total_weight = sum(max(len(text), 1) for text in texts)
    confidence = sum(
        line.confidence * max(len(text), 1)
        for line, text in zip(candidates, texts)
    ) / total_weight
    return "\n".join(texts), confidence


def select_subtitle_geometry(
    lines: Sequence[OCRLine],
    min_confidence: float,
    *,
    frame_width: int,
    frame_height: int,
) -> tuple[str, float, tuple[float, float, float, float] | None]:
    """Return text plus a normalized union bbox for the accepted Chinese lines."""

    candidates = [
        line
        for line in lines
        if line.confidence >= min_confidence and contains_cjk(line.text)
    ]
    text, confidence = select_subtitle_text(candidates, min_confidence)
    boxes = [line.bbox for line in candidates if line.bbox is not None]
    if not text or not boxes or frame_width <= 0 or frame_height <= 0:
        return text, confidence, None
    x1 = max(0.0, min(box[0] for box in boxes) / frame_width)
    y1 = max(0.0, min(box[1] for box in boxes) / frame_height)
    x2 = min(1.0, max(box[2] for box in boxes) / frame_width)
    y2 = min(1.0, max(box[3] for box in boxes) / frame_height)
    return text, confidence, (x1, y1, x2, y2)


def _choose_consensus(observations: Sequence[FrameObservation]) -> tuple[str, float, dict[str, int]]:
    grouped: dict[str, list[FrameObservation]] = defaultdict(list)
    display_text: dict[str, str] = {}
    for observation in observations:
        normalized = normalize_zh_text(observation.text)
        if not normalized:
            continue
        grouped[normalized].append(observation)
        display_text.setdefault(normalized, observation.text.strip())

    if not grouped:
        return "", 0.0, {}

    winner = max(
        grouped,
        key=lambda text: (
            len(grouped[text]),
            sum(item.confidence for item in grouped[text]) / len(grouped[text]),
            len(text),
        ),
    )
    winning_items = grouped[winner]
    confidence = sum(item.confidence for item in winning_items) / len(winning_items)
    variants = {display_text[text]: len(items) for text, items in grouped.items()}
    return display_text[winner], confidence, variants


def observations_to_cues(
    observations: Sequence[FrameObservation],
    config: OCRConfig,
) -> list[SubtitleCue]:
    """Consolidate noisy sampled OCR observations into timed subtitle cues."""

    config.validate()
    if not observations:
        return []

    ordered = sorted(observations, key=lambda item: item.timestamp)
    frame_duration = 1.0 / config.sample_fps
    cues: list[SubtitleCue] = []
    active: list[FrameObservation] = []
    blank_count = 0

    def flush() -> None:
        nonlocal active, blank_count
        if len(active) < config.min_stable_frames:
            active = []
            blank_count = 0
            return
        text, confidence, variants = _choose_consensus(active)
        if text:
            matching_boxes = [
                item.bbox
                for item in active
                if item.bbox is not None and text_similarity(item.text, text) >= config.text_similarity
            ]
            normalized_box = None
            if matching_boxes:
                crop_box = tuple(
                    median(box[index] for box in matching_boxes)
                    for index in range(4)
                )
                x1, y1, x2, y2 = crop_box
                roi_height = config.roi_bottom - config.roi_top
                normalized_box = (
                    x1,
                    config.roi_top + y1 * roi_height,
                    x2,
                    config.roi_top + y2 * roi_height,
                )
            cues.append(
                SubtitleCue(
                    start=max(0.0, active[0].timestamp - frame_duration / 2),
                    end=active[-1].timestamp + frame_duration / 2,
                    text=text,
                    confidence=confidence,
                    samples=len(active),
                    variants=variants,
                    bbox=normalized_box,
                    position_confidence=confidence if normalized_box else 0.0,
                )
            )
        active = []
        blank_count = 0

    for observation in ordered:
        text = normalize_zh_text(observation.text)
        if not text or observation.confidence < config.min_confidence:
            if active:
                blank_count += 1
                if blank_count > config.blank_tolerance_frames:
                    flush()
            continue

        if not active:
            active = [observation]
            blank_count = 0
            continue

        canonical, _, _ = _choose_consensus(active)
        if text_similarity(canonical, text) >= config.text_similarity:
            active.append(observation)
            blank_count = 0
            continue

        flush()
        active = [observation]

    flush()

    merged: list[SubtitleCue] = []
    max_reconnect_gap = frame_duration * (config.blank_tolerance_frames + 2)
    for cue in cues:
        if (
            merged
            and cue.start - merged[-1].end <= max_reconnect_gap
            and text_similarity(merged[-1].text, cue.text) >= config.text_similarity
        ):
            previous = merged[-1]
            total_samples = previous.samples + cue.samples
            previous.confidence = (
                previous.confidence * previous.samples + cue.confidence * cue.samples
            ) / total_samples
            previous.end = cue.end
            previous.samples = total_samples
            for variant, count in cue.variants.items():
                previous.variants[variant] = previous.variants.get(variant, 0) + count
        else:
            merged.append(cue)
    cues = merged

    # Guard against rounding/sampling noise creating overlaps at cue transitions.
    for previous, current in zip(cues, cues[1:]):
        if previous.end > current.start:
            boundary = (previous.end + current.start) / 2
            previous.end = boundary
            current.start = boundary
    return cues


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bbox_from_points(points: Any) -> tuple[float, float, float, float] | None:
    if points is None:
        return None
    try:
        flattened = list(points.tolist() if hasattr(points, "tolist") else points)
        if len(flattened) == 4 and all(not isinstance(item, (list, tuple)) for item in flattened):
            x1, y1, x2, y2 = map(float, flattened)
            return x1, y1, x2, y2
        xs = [float(point[0]) for point in flattened]
        ys = [float(point[1]) for point in flattened]
        return min(xs), min(ys), max(xs), max(ys)
    except (TypeError, ValueError, IndexError):
        return None


def _unwrap_v3_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        if isinstance(result.get("res"), dict):
            return result["res"]
        return result
    json_value = getattr(result, "json", None)
    if callable(json_value):
        json_value = json_value()
    if isinstance(json_value, dict):
        return json_value.get("res", json_value)
    res_value = getattr(result, "res", None)
    return res_value if isinstance(res_value, dict) else {}


def _result_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    return list(value)


class PaddleOCRReader:
    """Compatibility adapter for PaddleOCR 2.x and 3.x result formats."""

    def __init__(self, *, language: str = "ch", device: str = "cpu") -> None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:  # pragma: no cover - depends on deployment image
            raise RuntimeError(
                "PaddleOCR is not installed. Install the OCR dependencies or use subtitle_source=speech."
            ) from exc

        try:
            self._engine = PaddleOCR(
                lang=language,
                device=device,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            self._api_version = 3
        except TypeError:  # PaddleOCR 2.x
            self._engine = PaddleOCR(lang=language, use_angle_cls=False, use_gpu=device != "cpu")
            self._api_version = 2
        self._inference_lock = threading.Lock()

    def read(self, image_path: Path) -> list[OCRLine]:
        with self._inference_lock:
            if self._api_version == 3:
                return self._read_v3(image_path)
            return self._read_v2(image_path)

    def _read_v3(self, image_path: Path) -> list[OCRLine]:
        output = list(self._engine.predict(str(image_path)))
        lines: list[OCRLine] = []
        for item in output:
            data = _unwrap_v3_result(item)
            texts = _result_list(data, "rec_texts")
            scores = _result_list(data, "rec_scores")
            boxes = _result_list(data, "rec_boxes")
            if not boxes:
                boxes = _result_list(data, "rec_polys")
            for index, text in enumerate(texts):
                if not str(text).strip():
                    continue
                lines.append(
                    OCRLine(
                        text=str(text).strip(),
                        confidence=_to_float(scores[index] if index < len(scores) else 0),
                        bbox=_bbox_from_points(boxes[index] if index < len(boxes) else None),
                    )
                )
        return lines
    def _read_v2(self, image_path: Path) -> list[OCRLine]:
        output = self._engine.ocr(str(image_path), cls=False)
        raw_lines = output[0] if output and isinstance(output[0], list) else output
        lines: list[OCRLine] = []
        for item in raw_lines or []:
            try:
                points, recognition = item
                text, confidence = recognition
            except (TypeError, ValueError):
                continue
            if str(text).strip():
                lines.append(
                    OCRLine(
                        text=str(text).strip(),
                        confidence=_to_float(confidence),
                        bbox=_bbox_from_points(points),
                    )
                )
        return lines


_DEFAULT_READER: PaddleOCRReader | None = None
_DEFAULT_READER_LOCK = threading.Lock()


def get_default_reader() -> PaddleOCRReader:
    global _DEFAULT_READER
    if _DEFAULT_READER is None:
        with _DEFAULT_READER_LOCK:
            if _DEFAULT_READER is None:
                _DEFAULT_READER = PaddleOCRReader()
    return _DEFAULT_READER


def extract_ocr_frames(
    video_path: Path,
    frames_dir: Path,
    *,
    ffmpeg: str,
    config: OCRConfig,
) -> list[Path]:
    config.validate()
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    roi_height = config.roi_bottom - config.roi_top
    video_filter = (
        f"fps={config.sample_fps:.6f},"
        f"crop=iw:floor(ih*{roi_height:.6f}/2)*2:0:floor(ih*{config.roi_top:.6f}/2)*2,"
        f"scale=iw*{config.upscale:.3f}:ih*{config.upscale:.3f}:flags=lanczos"
    )
    output_pattern = frames_dir / "frame_%08d.jpg"
    result = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-vf",
            video_filter,
            "-vsync",
            "0",
            "-q:v",
            "2",
            str(output_pattern),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg could not extract OCR frames: {result.stderr[-2000:]}")
    return sorted(frames_dir.glob("frame_*.jpg"))


def scan_subtitle_frames(
    frame_paths: Sequence[Path],
    *,
    reader: OCRReader,
    config: OCRConfig,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[FrameObservation]:
    observations: list[FrameObservation] = []
    total = len(frame_paths)
    for index, frame_path in enumerate(frame_paths):
        lines = reader.read(frame_path)
        try:
            from PIL import Image

            with Image.open(frame_path) as image:
                frame_width, frame_height = image.size
        except Exception:
            frame_width, frame_height = 0, 0
        text, confidence, bbox = select_subtitle_geometry(
            lines,
            config.min_confidence,
            frame_width=frame_width,
            frame_height=frame_height,
        )
        observations.append(
            FrameObservation(
                timestamp=index / config.sample_fps,
                text=text,
                confidence=confidence,
                bbox=bbox,
            )
        )
        if progress_callback:
            progress_callback(index + 1, total)
    return observations


def extract_burned_subtitle_segments(
    video_path: Path,
    work_dir: Path,
    *,
    ffmpeg: str,
    config: OCRConfig | None = None,
    reader: OCRReader | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    config = config or OCRConfig()
    frame_paths = extract_ocr_frames(
        video_path,
        work_dir / "ocr_frames",
        ffmpeg=ffmpeg,
        config=config,
    )
    if not frame_paths:
        return []
    active_reader = reader or get_default_reader()
    try:
        observations = scan_subtitle_frames(
            frame_paths,
            reader=active_reader,
            config=config,
            progress_callback=progress_callback,
        )
        cues = observations_to_cues(observations, config)
    finally:
        shutil.rmtree(work_dir / "ocr_frames", ignore_errors=True)
    logger.info(
        "OCR subtitle scan produced %d cues from %d sampled frames",
        len(cues),
        len(frame_paths),
    )
    return [cue.as_segment() for cue in cues]
