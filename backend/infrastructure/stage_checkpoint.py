from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from infrastructure.artifact_io import write_json_atomic


MANIFEST_VERSION = 1
CHECKPOINT_STAGES = frozenset({"transcript", "translation", "tts", "audio_mix", "render"})


@dataclass(frozen=True)
class CheckpointHit:
    stage: str
    payload: Any
    checksum: str
    bundle_checksum: str
    metadata: dict[str, Any]


PayloadValidator = Callable[[Any], bool]
PayloadFactory = Callable[[], Any]


def sha256_file(
    path: Path,
    *,
    chunk_size: int = 1024 * 1024,
    check_active: Callable[[], None] | None = None,
) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            if check_active is not None:
                check_active()
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_payload(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class StageCheckpointStore:
    """Versioned, checksum-validated checkpoints scoped to one job directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "stage_manifest.json"

    def _safe_artifact(self, relative_path: str) -> Path | None:
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        return candidate

    def _empty_manifest(self) -> dict[str, Any]:
        return {"version": MANIFEST_VERSION, "stages": {}}

    def _read_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.is_file():
            return self._empty_manifest()
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_manifest()
        if not isinstance(payload, dict) or payload.get("version") != MANIFEST_VERSION:
            return self._empty_manifest()
        if not isinstance(payload.get("stages"), dict):
            return self._empty_manifest()
        return payload

    def commit_json(
        self,
        stage: str,
        *,
        fingerprint: str,
        artifact_name: str,
        payload: Any,
        metadata: dict[str, Any] | None = None,
        dependency_names: list[str] | None = None,
    ) -> CheckpointHit:
        if stage not in CHECKPOINT_STAGES:
            raise ValueError(f"unsupported checkpoint stage: {stage}")
        artifact = self._safe_artifact(artifact_name)
        if artifact is None or artifact == self.manifest_path:
            raise ValueError("checkpoint artifact must stay inside the job work directory")
        dependencies: list[dict[str, str]] = []
        seen_dependencies: set[str] = set()
        for dependency_name in dependency_names or []:
            dependency = self._safe_artifact(dependency_name)
            if dependency is None or dependency == self.manifest_path:
                raise ValueError("checkpoint dependency must stay inside the job work directory")
            relative_name = str(dependency.relative_to(self.root))
            if relative_name in seen_dependencies:
                continue
            if not dependency.is_file():
                raise ValueError(f"checkpoint dependency does not exist: {relative_name}")
            seen_dependencies.add(relative_name)
            dependencies.append({"path": relative_name, "checksum": sha256_file(dependency)})
        write_json_atomic(artifact, payload)
        checksum = sha256_file(artifact)
        bundle_checksum = fingerprint_payload(
            {"artifact_checksum": checksum, "dependencies": dependencies}
        )
        manifest = self._read_manifest()
        stages = dict(manifest["stages"])
        record = {
            "fingerprint": fingerprint,
            "artifact": str(artifact.relative_to(self.root)),
            "checksum": checksum,
            "dependencies": dependencies,
            "bundle_checksum": bundle_checksum,
            "metadata": dict(metadata or {}),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        stages[stage] = record
        write_json_atomic(
            self.manifest_path,
            {"version": MANIFEST_VERSION, "stages": stages},
        )
        return CheckpointHit(stage, payload, checksum, bundle_checksum, dict(record["metadata"]))

    def load_json(
        self,
        stage: str,
        *,
        fingerprint: str,
        validator: PayloadValidator | None = None,
    ) -> CheckpointHit | None:
        if stage not in CHECKPOINT_STAGES:
            raise ValueError(f"unsupported checkpoint stage: {stage}")
        record = self._read_manifest()["stages"].get(stage)
        if not isinstance(record, dict) or record.get("fingerprint") != fingerprint:
            return None
        artifact_name = record.get("artifact")
        checksum = record.get("checksum")
        if not isinstance(artifact_name, str) or not isinstance(checksum, str):
            return None
        artifact = self._safe_artifact(artifact_name)
        if artifact is None or not artifact.is_file():
            return None
        try:
            if sha256_file(artifact) != checksum:
                return None
            dependencies = record.get("dependencies", [])
            if not isinstance(dependencies, list):
                return None
            normalized_dependencies: list[dict[str, str]] = []
            for dependency_record in dependencies:
                if not isinstance(dependency_record, dict):
                    return None
                dependency_name = dependency_record.get("path")
                dependency_checksum = dependency_record.get("checksum")
                if not isinstance(dependency_name, str) or not isinstance(dependency_checksum, str):
                    return None
                dependency = self._safe_artifact(dependency_name)
                if dependency is None or not dependency.is_file():
                    return None
                if sha256_file(dependency) != dependency_checksum:
                    return None
                normalized_dependencies.append(
                    {"path": dependency_name, "checksum": dependency_checksum}
                )
            bundle_checksum = fingerprint_payload(
                {"artifact_checksum": checksum, "dependencies": normalized_dependencies}
            )
            recorded_bundle_checksum = record.get("bundle_checksum")
            if recorded_bundle_checksum is not None and recorded_bundle_checksum != bundle_checksum:
                return None
            payload = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if validator is not None and not validator(payload):
            return None
        metadata = record.get("metadata")
        return CheckpointHit(
            stage=stage,
            payload=payload,
            checksum=checksum,
            bundle_checksum=bundle_checksum,
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
        )


def load_or_compute_json(
    store: StageCheckpointStore,
    stage: str,
    *,
    fingerprint: str,
    artifact_name: str,
    validator: PayloadValidator,
    compute: PayloadFactory,
    metadata: dict[str, Any] | None = None,
) -> tuple[CheckpointHit, bool]:
    """Return a valid checkpoint or atomically commit a freshly computed payload."""

    hit = store.load_json(stage, fingerprint=fingerprint, validator=validator)
    if hit is not None:
        return hit, True
    payload = compute()
    if not validator(payload):
        raise ValueError(f"computed {stage} checkpoint payload is invalid")
    return (
        store.commit_json(
            stage,
            fingerprint=fingerprint,
            artifact_name=artifact_name,
            payload=payload,
            metadata=metadata,
        ),
        False,
    )
