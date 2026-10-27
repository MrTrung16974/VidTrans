from __future__ import annotations

from pathlib import Path
from typing import Any


TTS_STATE_KEYS = (
    "voice_type",
    "voice_routing",
    "tts_speed_factor",
    "tts_catch_up_tempo",
    "tts_voice_style",
    "tts_voice_name",
    "tts_voice_rate",
    "tts_voice_pitch",
    "tts_duration",
    "tts_target_duration",
    "tts_overrun",
    "tts_fit_quality",
    "tts_start",
    "tts_end",
    "tts_drift",
)
TTS_STATE_KEY_SET = frozenset(TTS_STATE_KEYS)


def valid_checkpoint_segments(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    return all(
        isinstance(segment, dict)
        and "start" in segment
        and "end" in segment
        and isinstance(segment.get("text"), str)
        for segment in value
    )


def valid_transcript_checkpoint(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("version") == 1
        and value.get("source_method") in {"ocr", "speech", "hybrid", "independent"}
        and valid_checkpoint_segments(value.get("segments"))
    )


def valid_translation_checkpoint(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("version") != 1:
        return False
    segments = value.get("segments")
    if not valid_checkpoint_segments(segments):
        return False
    active = [segment for segment in segments if not segment.get("skip_subtitle")]
    return bool(active) and all(
        segment.get("translation_status") not in {"source_fallback", "failed"}
        and bool(str(segment.get("text") or "").strip())
        for segment in active
    )


def valid_tts_checkpoint(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("version") != 1:
        return False
    segment_count = value.get("segment_count")
    states = value.get("states")
    if not isinstance(segment_count, int) or segment_count < 0 or not isinstance(states, list):
        return False
    if len(states) != segment_count or not isinstance(value.get("voice_track"), str):
        return False
    return all(
        isinstance(state, dict)
        and state.get("index") == index
        and isinstance(state.get("values"), dict)
        and set(state["values"]).issubset(TTS_STATE_KEY_SET | {"tts_path"})
        and ("tts_path" not in state["values"] or isinstance(state["values"]["tts_path"], str))
        for index, state in enumerate(states)
    )


def valid_file_checkpoint(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("version") == 1
        and isinstance(value.get("artifact"), str)
        and bool(value["artifact"])
    )


def restore_file_checkpoint(payload: dict[str, Any], *, work_dir: Path) -> Path:
    if not valid_file_checkpoint(payload):
        raise ValueError("invalid file checkpoint")
    root = Path(work_dir).resolve()
    artifact = (root / payload["artifact"]).resolve()
    try:
        artifact.relative_to(root)
    except ValueError as exc:
        raise ValueError("checkpoint artifact escapes the job work directory") from exc
    return artifact


def serialize_tts_checkpoint(
    segments: list[dict[str, Any]], *, work_dir: Path, voice_track: Path
) -> tuple[dict[str, Any], list[str]]:
    root = Path(work_dir).resolve()
    dependencies: list[str] = []
    states: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        values = {key: segment[key] for key in TTS_STATE_KEYS if key in segment}
        tts_path = segment.get("tts_path")
        if tts_path:
            resolved_tts_path = Path(tts_path).resolve()
            try:
                relative_tts_path = str(resolved_tts_path.relative_to(root))
            except ValueError as exc:
                raise ValueError("TTS artifact must stay inside the job work directory") from exc
            values["tts_path"] = relative_tts_path
            dependencies.append(relative_tts_path)
        states.append({"index": index, "values": values})
    resolved_voice_track = Path(voice_track).resolve()
    try:
        relative_voice_track = str(resolved_voice_track.relative_to(root))
    except ValueError as exc:
        raise ValueError("voice track must stay inside the job work directory") from exc
    dependencies.append(relative_voice_track)
    return (
        {
            "version": 1,
            "segment_count": len(segments),
            "states": states,
            "voice_track": relative_voice_track,
        },
        dependencies,
    )


def restore_tts_checkpoint(
    segments: list[dict[str, Any]], payload: dict[str, Any], *, work_dir: Path
) -> Path:
    if not valid_tts_checkpoint(payload) or payload["segment_count"] != len(segments):
        raise ValueError("TTS checkpoint does not match current segments")
    root = Path(work_dir).resolve()

    def resolve_artifact(relative_name: str) -> Path:
        artifact = (root / relative_name).resolve()
        try:
            artifact.relative_to(root)
        except ValueError as exc:
            raise ValueError("TTS checkpoint artifact escapes the job work directory") from exc
        return artifact

    for segment, state in zip(segments, payload["states"]):
        values = dict(state["values"])
        tts_path = values.pop("tts_path", None)
        segment.update(values)
        if tts_path is not None:
            segment["tts_path"] = resolve_artifact(tts_path)
    return resolve_artifact(payload["voice_track"])
