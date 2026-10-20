from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
import threading
import unicodedata
import csv
import io
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field, replace
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
    # Adjacent cues of equal length at the same position whose text is at
    # least this similar are one caption read twice (typically a misread
    # during a fade-in), not two captions.
    variant_similarity: float = 0.6
    blank_tolerance_frames: int = 1
    min_stable_frames: int = 2
    upscale: float = 2.0
    # Frames at least this wide are read as-is: upscaling a 1080p strip made
    # PaddleOCR ~3x slower on CPU without measurable accuracy gain.
    upscale_below_width: int = 1000
    # Sampled OCR only knows a cue boundary to within one sample interval.
    # A cheap low-resolution pass at this rate snaps boundaries to the frame
    # where the caption actually appears and disappears.
    refine_boundaries: bool = True
    refine_fps: float = 30.0
    refine_width: int = 640
    refine_min_overlap: float = 0.5

    def inference_scale(self, frame_width: int) -> float:
        if frame_width <= 0 or frame_width >= self.upscale_below_width:
            return 1.0
        return max(1.0, min(self.upscale, self.upscale_below_width / frame_width))

    def validate(self) -> None:
        if not 0 < self.sample_fps <= 30:
            raise ValueError("ocr sample_fps must be in (0, 30]")
        if not 0 <= self.roi_top < self.roi_bottom <= 1:
            raise ValueError("OCR ROI must satisfy 0 <= top < bottom <= 1")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("ocr min_confidence must be in [0, 1]")
        if not 0 <= self.text_similarity <= 1:
            raise ValueError("ocr text_similarity must be in [0, 1]")
        if not 0 <= self.variant_similarity <= 1:
            raise ValueError("ocr variant_similarity must be in [0, 1]")
        if not 0 < self.refine_fps <= 120:
            raise ValueError("ocr refine_fps must be in (0, 120]")
        if self.refine_width < 32:
            raise ValueError("ocr refine_width must be at least 32")
        if not 0 < self.refine_min_overlap <= 1:
            raise ValueError("ocr refine_min_overlap must be in (0, 1]")
        if self.blank_tolerance_frames < 0:
            raise ValueError("ocr blank_tolerance_frames cannot be negative")
        if self.min_stable_frames < 1:
            raise ValueError("ocr min_stable_frames must be at least 1")
        if self.upscale < 1:
            raise ValueError("ocr upscale must be at least 1")
        if self.upscale_below_width < 0:
            raise ValueError("ocr upscale_below_width cannot be negative")


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
    # Timestamps of the first and last sampled frames that showed the cue.
    first_sample: float | None = None
    last_sample: float | None = None

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


def merge_ocr_and_asr_segments(
    ocr_segments: list[dict[str, Any]],
    asr_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combine visible captions with speech cues, preferring OCR on overlap.

    Burned captions can remain on screen while the soundtrack is silent.  OCR
    therefore supplies cues that ASR cannot produce, while non-overlapping ASR
    cues preserve dialogue that has no visible caption.
    """

    annotated_ocr = annotate_ocr_segments_with_asr(ocr_segments, asr_segments)
    for cue in annotated_ocr:
        cue.setdefault("source_method", "ocr")
    merged: list[dict[str, Any]] = list(annotated_ocr)
    for segment in asr_segments:
        start = float(segment["start"])
        end = float(segment["end"])
        duration = max(end - start, 0.001)
        covered_by_ocr = any(
            max(
                0.0,
                min(end, float(cue["end"])) - max(start, float(cue["start"])),
            )
            >= min(0.5, duration * 0.35)
            for cue in annotated_ocr
        )
        if not covered_by_ocr:
            segment.setdefault("source_method", "speech")
            merged.append(segment)

    return sorted(
        merged,
        key=lambda item: (float(item["start"]), float(item["end"])),
    )


def merge_independent_ocr_asr_segments(
    ocr_segments: list[dict[str, Any]],
    asr_segments: list[dict[str, Any]],
    *,
    ocr_language: str = "zh-CN",
    asr_language: str | None = None,
) -> list[dict[str, Any]]:
    """Merge independent visual/audio sources without cross-language comparison."""

    visual = []
    for cue in ocr_segments:
        item = dict(cue)
        item["source_method"] = "ocr"
        item["source_language"] = ocr_language
        item["modality"] = "visual"
        visual.append(item)

    merged: list[dict[str, Any]] = list(visual)
    for cue in asr_segments:
        start = float(cue["start"])
        end = float(cue["end"])
        duration = max(end - start, 0.001)
        covered_by_visual = any(
            max(0.0, min(end, float(item["end"])) - max(start, float(item["start"])))
            >= min(0.5, duration * 0.35)
            for item in visual
        )
        if covered_by_visual:
            continue
        item = dict(cue)
        item["source_method"] = "speech"
        item["source_language"] = asr_language or "auto"
        item["modality"] = "audio"
        if not asr_language:
            item["needs_review"] = True
        merged.append(item)

    return sorted(merged, key=lambda item: (float(item["start"]), float(item["end"])))


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


def _bbox_overlap(
    left: tuple[float, float, float, float] | None,
    right: tuple[float, float, float, float] | None,
) -> float:
    if left is None or right is None:
        return 1.0
    width = min(left[2], right[2]) - max(left[0], right[0])
    height = min(left[3], right[3]) - max(left[1], right[1])
    if width <= 0 or height <= 0:
        return 0.0
    intersection = width * height
    union = (
        (left[2] - left[0]) * (left[3] - left[1])
        + (right[2] - right[0]) * (right[3] - right[1])
        - intersection
    )
    return intersection / union if union > 0 else 0.0


def _is_misread_variant(previous: SubtitleCue, current: SubtitleCue, config: OCRConfig) -> bool:
    """Whether two adjacent cues are the same on-screen caption read differently."""

    left = normalize_zh_text(previous.text)
    right = normalize_zh_text(current.text)
    # Misreads substitute glyphs; a caption that grows or shrinks by a
    # character ("爸好看呢" -> "身体好看呢") is a new line.
    if len(left) < 2 or len(left) != len(right):
        return False
    return (
        text_similarity(left, right) >= config.variant_similarity
        and _bbox_overlap(previous.bbox, current.bbox) >= 0.5
    )


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
            # ffmpeg's fps filter fills sample k with the last source frame
            # before (k + 0.5) / fps, so a sample shows the picture half an
            # interval after its nominal timestamp. Boundaries are therefore
            # midway between samples measured on that shifted clock.
            cues.append(
                SubtitleCue(
                    start=max(0.0, active[0].timestamp),
                    end=active[-1].timestamp + frame_duration,
                    text=text,
                    confidence=confidence,
                    samples=len(active),
                    variants=variants,
                    bbox=normalized_box,
                    position_confidence=confidence if normalized_box else 0.0,
                    first_sample=active[0].timestamp,
                    last_sample=active[-1].timestamp,
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
            and (
                text_similarity(merged[-1].text, cue.text) >= config.text_similarity
                or _is_misread_variant(merged[-1], cue, config)
            )
        ):
            previous = merged[-1]
            total_samples = previous.samples + cue.samples
            previous.confidence = (
                previous.confidence * previous.samples + cue.confidence * cue.samples
            ) / total_samples
            previous.end = cue.end
            previous.last_sample = cue.last_sample
            if cue.samples > previous.samples and cue.bbox is not None:
                previous.bbox = cue.bbox
                previous.position_confidence = cue.position_confidence
            previous.samples = total_samples
            for variant, count in cue.variants.items():
                previous.variants[variant] = previous.variants.get(variant, 0) + count
            # The most frequently read variant wins; earlier reads win ties.
            previous.text = max(
                previous.variants,
                key=lambda variant: previous.variants[variant],
            )
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


# "medium" keeps PaddleOCR's default models. "mobile" was ~5.8x faster on CPU
# in a synthetic Chinese subtitle benchmark at the cost of a few misread glyphs.
PADDLE_MODEL_NAMES: dict[str, dict[str, str]] = {
    "medium": {},
    "mobile": {
        "text_detection_model_name": "PP-OCRv5_mobile_det",
        "text_recognition_model_name": "PP-OCRv5_mobile_rec",
    },
}


class PaddleOCRReader:
    """Compatibility adapter for PaddleOCR 2.x and 3.x result formats."""

    def __init__(self, *, language: str = "ch", device: str = "cpu", model: str = "medium") -> None:
        if model not in PADDLE_MODEL_NAMES:
            raise ValueError(f"OCR model must be one of: {', '.join(PADDLE_MODEL_NAMES)}")
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
                **PADDLE_MODEL_NAMES[model],
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


class TesseractOCRReader:
    """Chinese OCR adapter backed by the stable Tesseract CLI."""

    def __init__(self, *, executable: str = "tesseract", language: str = "chi_sim") -> None:
        resolved = shutil.which(executable)
        if not resolved:
            raise RuntimeError(
                "Tesseract OCR is not installed. Rebuild the image with tesseract-ocr-chi-sim."
            )
        self._executable = resolved
        self._language = language

    def read(self, image_path: Path) -> list[OCRLine]:
        return self.read_many([image_path])[0]

    def read_many(self, image_paths: Sequence[Path]) -> list[list[OCRLine]]:
        if not image_paths:
            return []
        if len(image_paths) == 1:
            input_path = str(image_paths[0])
            list_path: Path | None = None
        else:
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".txt",
                prefix="vidtrans_tesseract_",
                delete=False,
                dir=image_paths[0].parent,
            )
            list_path = Path(handle.name)
            try:
                handle.write("".join(f"{path.resolve()}\n" for path in image_paths))
            finally:
                handle.close()
            input_path = str(list_path)
        try:
            result = self._run(input_path)
        finally:
            if list_path is not None:
                list_path.unlink(missing_ok=True)
        return self._parse_tsv_pages(result.stdout, len(image_paths))

    def _run(self, input_path: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [
                self._executable,
                input_path,
                "stdout",
                "-l",
                self._language,
                "--psm",
                "6",
                "tsv",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or "").strip()[-600:]
            raise RuntimeError(f"Tesseract OCR failed: {detail}")
        return result

    @staticmethod
    def _parse_tsv_pages(tsv: str, page_count: int) -> list[list[OCRLine]]:
        grouped: dict[
            tuple[int, str, str, str],
            list[tuple[str, float, tuple[float, float, float, float]]],
        ] = defaultdict(list)
        for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
            text = str(row.get("text") or "").strip()
            confidence = _to_float(row.get("conf"), -1.0)
            if not text or confidence < 0:
                continue
            left = _to_float(row.get("left"))
            top = _to_float(row.get("top"))
            width = _to_float(row.get("width"))
            height = _to_float(row.get("height"))
            key = (
                max(int(_to_float(row.get("page_num"), 1.0)) - 1, 0),
                str(row.get("block_num") or "0"),
                str(row.get("par_num") or "0"),
                str(row.get("line_num") or "0"),
            )
            grouped[key].append((text, confidence / 100.0, (left, top, left + width, top + height)))

        pages: list[list[OCRLine]] = [[] for _ in range(page_count)]
        for (page_index, _block, _paragraph, _line), words in grouped.items():
            if page_index >= page_count:
                continue
            texts = [word[0] for word in words]
            separator = "" if any(contains_cjk(text) for text in texts) else " "
            boxes = [word[2] for word in words]
            total_chars = sum(max(len(word[0]), 1) for word in words)
            confidence = sum(word[1] * max(len(word[0]), 1) for word in words) / total_chars
            pages[page_index].append(
                OCRLine(
                    separator.join(texts),
                    confidence,
                    (
                        min(box[0] for box in boxes),
                        min(box[1] for box in boxes),
                        max(box[2] for box in boxes),
                        max(box[3] for box in boxes),
                    ),
                )
            )
        return pages


_DEFAULT_READERS: dict[tuple[str, str], OCRReader] = {}
_DEFAULT_READER_LOCK = threading.Lock()


def get_default_reader(backend: str = "paddle", model: str = "medium") -> OCRReader:
    if backend not in {"paddle", "tesseract"}:
        raise ValueError("OCR backend must be paddle or tesseract")
    key = (backend, model if backend == "paddle" else "")
    if key not in _DEFAULT_READERS:
        with _DEFAULT_READER_LOCK:
            if key not in _DEFAULT_READERS:
                _DEFAULT_READERS[key] = (
                    PaddleOCRReader(model=model) if backend == "paddle" else TesseractOCRReader()
                )
    return _DEFAULT_READERS[key]


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
        f"crop=iw:floor(ih*{roi_height:.6f}/2)*2:0:floor(ih*{config.roi_top:.6f}/2)*2"
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


def _subtitle_change_signature(image: Any, max_width: int = 320) -> Any:
    """Return a compact mask biased toward bright, high-contrast subtitle strokes."""

    import numpy as np
    from PIL import ImageFilter

    grayscale = image.convert("L")
    target_width = min(grayscale.width, max_width)
    target_height = max(1, round(grayscale.height * target_width / max(grayscale.width, 1)))
    if grayscale.size != (target_width, target_height):
        grayscale = grayscale.resize((target_width, target_height))
    local_max = grayscale.filter(ImageFilter.MaxFilter(5))
    local_min = grayscale.filter(ImageFilter.MinFilter(5))
    pixels = np.asarray(grayscale, dtype=np.int16)
    contrast = np.asarray(local_max, dtype=np.int16) - np.asarray(local_min, dtype=np.int16)
    return (pixels >= 150) & (contrast >= 55)


def _signatures_are_similar(previous: Any, current: Any) -> bool:
    import numpy as np

    if previous is None or current is None or previous.shape != current.shape:
        return False
    union = np.count_nonzero(previous | current)
    if union == 0:
        return True
    changed = np.count_nonzero(previous ^ current)
    return changed / union < 0.12


def _mask_overlap(left: Any, right: Any) -> float:
    import numpy as np

    union = np.count_nonzero(left | right)
    if union == 0:
        return 1.0
    return np.count_nonzero(left & right) / union


def _cue_signature_box(
    cue: SubtitleCue,
    config: OCRConfig,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    """Map a cue bbox to padded signature pixels so background motion is ignored."""

    if cue.bbox is None:
        return 0, 0, width, height
    x1, y1, x2, y2 = cue.bbox
    roi_height = config.roi_bottom - config.roi_top
    top = (y1 - config.roi_top) / roi_height * height
    bottom = (y2 - config.roi_top) / roi_height * height
    pad = max(2.0, (bottom - top) * 0.35)
    left = int(max(0, math.floor(x1 * width - pad)))
    right = int(min(width, math.ceil(x2 * width + pad)))
    upper = int(max(0, math.floor(top - pad)))
    lower = int(min(height, math.ceil(bottom + pad)))
    if right - left < 2 or lower - upper < 2:
        return 0, 0, width, height
    return left, upper, right, lower


def refine_cue_boundaries(
    cues: list[SubtitleCue],
    video_path: Path,
    *,
    ffmpeg: str,
    config: OCRConfig,
    roi_size: tuple[int, int],
) -> list[SubtitleCue]:
    """Snap sampled cue boundaries to the frames where the caption changes.

    Each boundary lies between a sample that showed the cue and its neighbour
    sample. Only frames inside those intervals are kept, as bit-packed
    stroke masks, so memory stays small on long videos. A frame belongs to
    the cue while its strokes inside the cue bbox overlap the anchor sample.
    """

    import numpy as np
    from PIL import Image

    if not cues or roi_size[0] <= 0 or roi_size[1] <= 0:
        return cues
    rate = config.refine_fps
    step = 1.0 / config.sample_fps
    width = min(config.refine_width, roi_size[0])
    height = max(2, round(roi_size[1] * width / roi_size[0] / 2) * 2)

    def shown_frame(sample_time: float) -> int:
        # Both decodes use the fps filter, which keeps the last source frame
        # before the middle of each output slot; map a sample to that frame.
        return max(0, math.ceil((sample_time + step / 2) * rate - 0.5 - 1e-6) - 1)

    # (cue index, direction, first frame, last frame, anchor frame)
    probes: list[tuple[int, int, int, int, int]] = []
    for index, cue in enumerate(cues):
        if cue.first_sample is None or cue.last_sample is None:
            continue
        anchor = shown_frame(cue.first_sample)
        probes.append((index, -1, shown_frame(cue.first_sample - step) + 1, anchor, anchor))
        anchor = shown_frame(cue.last_sample)
        probes.append((index, 1, anchor, shown_frame(cue.last_sample + step) - 1, anchor))
    if not probes:
        return cues
    needed = {frame for probe in probes for frame in range(probe[2], probe[3] + 1)}
    last_needed = max(needed)

    roi_height = config.roi_bottom - config.roi_top
    video_filter = (
        f"fps={rate:.6f},"
        f"crop=iw:floor(ih*{roi_height:.6f}/2)*2:0:floor(ih*{config.roi_top:.6f}/2)*2,"
        f"scale={width}:{height},format=gray"
    )
    process = subprocess.Popen(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(video_path),
            "-vf",
            video_filter,
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    frame_bytes = width * height
    packed: dict[int, Any] = {}
    try:
        assert process.stdout is not None
        frame_index = 0
        while frame_index <= last_needed:
            buffer = process.stdout.read(frame_bytes)
            if len(buffer) < frame_bytes:
                break
            if frame_index in needed:
                image = Image.frombytes("L", (width, height), buffer)
                packed[frame_index] = np.packbits(_subtitle_change_signature(image, width))
            frame_index += 1
    finally:
        if process.stdout is not None:
            process.stdout.close()
        process.kill()
        process.wait()

    def mask(frame: int, box: tuple[int, int, int, int]) -> Any | None:
        if frame not in packed:
            return None
        full = np.unpackbits(packed[frame], count=width * height).reshape(height, width).astype(bool)
        left, upper, right, lower = box
        return full[upper:lower, left:right]

    refined = [replace(cue) for cue in cues]
    for index, direction, first, last, anchor in probes:
        box = _cue_signature_box(cues[index], config, width, height)
        reference = mask(anchor, box)
        if reference is None or np.count_nonzero(reference) < 8:
            continue
        frame = anchor
        while first <= frame + direction <= last:
            candidate = mask(frame + direction, box)
            if candidate is None or _mask_overlap(candidate, reference) < config.refine_min_overlap:
                break
            frame += direction
        if direction < 0:
            refined[index].start = max(0.0, frame / rate)
        else:
            refined[index].end = (frame + 1) / rate

    for cue in refined:
        cue.end = max(cue.end, cue.start + 1.0 / rate)
    for previous, current in zip(refined, refined[1:]):
        if previous.end > current.start:
            boundary = (previous.end + current.start) / 2
            previous.end = boundary
            current.start = boundary
    return refined


def _prepare_inference_images(
    frame_paths: Sequence[Path],
    *,
    scale: float,
) -> tuple[list[Path], list[Path]]:
    if scale <= 1.0:
        return list(frame_paths), []

    from PIL import Image

    prepared: list[Path] = []
    temporary: list[Path] = []
    for frame_path in frame_paths:
        output_path = frame_path.with_name(f"ocr_input_{frame_path.stem}.jpg")
        with Image.open(frame_path) as image:
            width = max(1, round(image.width * scale))
            height = max(1, round(image.height * scale))
            image.resize((width, height), Image.Resampling.LANCZOS).save(
                output_path,
                quality=92,
                optimize=True,
            )
        prepared.append(output_path)
        temporary.append(output_path)
    return prepared, temporary


def scan_subtitle_frames(
    frame_paths: Sequence[Path],
    *,
    reader: OCRReader,
    config: OCRConfig,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[FrameObservation]:
    observations: list[FrameObservation] = []
    from PIL import Image

    previous_signature = None
    last_ocr_index = -1
    frame_metadata: list[tuple[int, int]] = []
    ocr_indexes: list[int] = []
    forced_refresh_frames = max(1, math.ceil(config.sample_fps * 1.5))
    inference_scale: float | None = None

    for index, frame_path in enumerate(frame_paths):
        try:
            with Image.open(frame_path) as image:
                if inference_scale is None:
                    inference_scale = config.inference_scale(image.width)
                frame_width = max(1, round(image.width * inference_scale))
                frame_height = max(1, round(image.height * inference_scale))
                current_signature = _subtitle_change_signature(image)
        except Exception:
            frame_width, frame_height = 0, 0
            current_signature = None

        is_similar = _signatures_are_similar(previous_signature, current_signature)
        refresh_due = index - last_ocr_index >= forced_refresh_frames

        if not is_similar or refresh_due:
            previous_signature = current_signature
            last_ocr_index = index
            ocr_indexes.append(index)
        frame_metadata.append((frame_width, frame_height))

    lines_by_index: dict[int, list[OCRLine]] = {}
    read_many = getattr(reader, "read_many", None)
    # Readers without a batch API run frame by frame, so report after each
    # frame: the progress callback is also where cancellation is honoured.
    batch_size = 32 if callable(read_many) else 1
    total_ocr = len(ocr_indexes)
    for offset in range(0, total_ocr, batch_size):
        batch_indexes = ocr_indexes[offset : offset + batch_size]
        batch_paths = [frame_paths[index] for index in batch_indexes]
        inference_paths, temporary_paths = _prepare_inference_images(
            batch_paths,
            scale=inference_scale or 1.0,
        )
        try:
            if callable(read_many):
                batch_results = read_many(inference_paths)
            else:
                batch_results = [reader.read(path) for path in inference_paths]
        finally:
            for temporary_path in temporary_paths:
                temporary_path.unlink(missing_ok=True)
        if len(batch_results) != len(batch_indexes):
            raise RuntimeError("OCR batch result count does not match input frame count")
        lines_by_index.update(zip(batch_indexes, batch_results))
        if progress_callback:
            progress_callback(min(offset + len(batch_indexes), total_ocr), total_ocr)

    previous_lines: list[OCRLine] = []
    for index, _frame_path in enumerate(frame_paths):
        frame_width, frame_height = frame_metadata[index]
        if index in lines_by_index:
            previous_lines = lines_by_index[index]
        lines = previous_lines
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
    return observations


def extract_burned_subtitle_segments(
    video_path: Path,
    work_dir: Path,
    *,
    ffmpeg: str,
    config: OCRConfig | None = None,
    reader: OCRReader | None = None,
    reader_backend: str = "paddle",
    reader_model: str = "medium",
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    config = config or OCRConfig()
    if reader is None and reader_backend == "tesseract" and config.sample_fps > 3.0:
        logger.info(
            "Capping Tesseract OCR sampling from %.2f fps to 3.00 fps",
            config.sample_fps,
        )
        config = replace(config, sample_fps=3.0)
    frame_paths = extract_ocr_frames(
        video_path,
        work_dir / "ocr_frames",
        ffmpeg=ffmpeg,
        config=config,
    )
    if not frame_paths:
        return []
    active_reader = reader or get_default_reader(reader_backend, reader_model)
    try:
        observations = scan_subtitle_frames(
            frame_paths,
            reader=active_reader,
            config=config,
            progress_callback=progress_callback,
        )
        cues = observations_to_cues(observations, config)
        if config.refine_boundaries and cues:
            try:
                from PIL import Image

                with Image.open(frame_paths[0]) as first_frame:
                    roi_size = first_frame.size
                cues = refine_cue_boundaries(
                    cues,
                    video_path,
                    ffmpeg=ffmpeg,
                    config=config,
                    roi_size=roi_size,
                )
            except Exception:
                logger.warning("OCR cue boundary refinement failed; keeping sampled timing", exc_info=True)
    finally:
        shutil.rmtree(work_dir / "ocr_frames", ignore_errors=True)
    logger.info(
        "OCR subtitle scan produced %d cues from %d sampled frames",
        len(cues),
        len(frame_paths),
    )
    return [cue.as_segment() for cue in cues]
