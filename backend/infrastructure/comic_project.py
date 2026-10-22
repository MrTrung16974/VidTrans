"""On-disk layout of one comic-video project (one job).

```
comic/
  story.txt            normalised source text
  storyboard.json      current editable storyboard
  history/rNNNN.json   snapshot of every saved revision
  manifest.json        what the last render produced, per scene
  refs/ images/ audio/ subs/ clips/   content-addressed caches
  keyframes/ animations/              animated mode: per-shot first frames and generated motion
  uploads/             user-supplied scene images
```
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from domain.comic import Storyboard
from infrastructure.artifact_io import write_json_atomic

HISTORY_LIMIT = 50
_ASSET_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
ASSET_KINDS = ("refs", "images", "uploads", "keyframes", "animations", "clips")


class ComicProject:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def dir(self, name: str) -> Path:
        path = self.root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def story_path(self) -> Path:
        return self.root / "story.txt"

    @property
    def storyboard_path(self) -> Path:
        return self.root / "storyboard.json"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def write_story(self, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.story_path.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(self.story_path)

    def read_story(self) -> str:
        return self.story_path.read_text(encoding="utf-8")

    def has_storyboard(self) -> bool:
        return self.storyboard_path.is_file()

    def load_storyboard(self) -> Storyboard:
        return Storyboard.model_validate_json(self.storyboard_path.read_text(encoding="utf-8"))

    def save_storyboard(self, storyboard: Storyboard, *, bump: bool = True) -> Storyboard:
        if bump and self.storyboard_path.is_file():
            storyboard.revision = self.load_storyboard().revision + 1
        payload = storyboard.model_dump(mode="json")
        write_json_atomic(self.storyboard_path, payload)
        history = self.dir("history")
        write_json_atomic(history / f"r{storyboard.revision:04d}.json", payload)
        for old in sorted(history.glob("r*.json"))[:-HISTORY_LIMIT]:
            old.unlink(missing_ok=True)
        return storyboard

    def reset_storyboard(self) -> None:
        self.storyboard_path.unlink(missing_ok=True)

    def load_manifest(self) -> dict[str, Any]:
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_manifest(self, manifest: dict[str, Any]) -> None:
        write_json_atomic(self.manifest_path, manifest)

    def asset_path(self, kind: str, name: str) -> Path | None:
        """Resolve a client-supplied asset reference, refusing anything outside the project."""
        if kind not in ASSET_KINDS or not _ASSET_NAME.fullmatch(name) or name.startswith("."):
            return None
        path = (self.root / kind / name).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            return None
        return path
