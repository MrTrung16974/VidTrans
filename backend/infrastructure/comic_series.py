"""On-disk store of comic series: one story told across many episode videos.

An episode is an ordinary comic project (one job); the series keeps what every
episode must share so the story stays consistent from video to video:

```
comic-series/<series_id>/
  series.json   title, description, default settings, character bible, world line,
                planned locations, episode list
  refs/         approved character reference images, shared by every episode
```

Episodes read the character bible before their storyboard is written (the LLM
reuses known ids and looks) and copy the pinned references into their own
project. After an episode is analysed or rendered, characters it introduced and
references it produced flow back into the bible; existing profiles are never
overwritten from an episode, so the series stays the single source of truth.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from domain.comic import ComicCharacter, ComicLocation, Storyboard
from infrastructure.artifact_io import write_json_atomic
from infrastructure.comic_project import ComicProject

_SERIES_ID = re.compile(r"^[0-9a-f]{12}$")
_REF_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
# Serialises read-modify-write of series.json between API requests and job workers.
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ComicSeries:
    def __init__(self, root: Path, data: dict[str, Any]) -> None:
        self.root = Path(root)
        self.data = data

    @property
    def id(self) -> str:
        return str(self.data["id"])

    @property
    def refs_dir(self) -> Path:
        path = self.root / "refs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def characters(self) -> list[ComicCharacter]:
        return [ComicCharacter.model_validate(item) for item in self.data.get("characters", [])]

    def character(self, character_id: str) -> ComicCharacter | None:
        return next((c for c in self.characters if c.id == character_id), None)

    def set_characters(self, characters: list[ComicCharacter]) -> None:
        self.data["characters"] = [c.model_dump(mode="json") for c in characters]

    @property
    def locations(self) -> list[ComicLocation]:
        return [ComicLocation.model_validate(item) for item in self.data.get("locations", [])]

    def set_locations(self, locations: list[ComicLocation]) -> None:
        self.data["locations"] = [place.model_dump(mode="json") for place in locations]

    @property
    def world(self) -> str:
        return str(self.data.get("world") or "")

    def ref_path(self, name: str | None) -> Path | None:
        if not name or not _REF_NAME.fullmatch(name) or name.startswith("."):
            return None
        path = (self.root / "refs" / name).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            return None
        return path

    def episode_job_ids(self) -> list[str]:
        return [str(item["job_id"]) for item in self.data.get("episodes", [])]

    def episode_number(self, job_id: str) -> int | None:
        return next((int(e["number"]) for e in self.data.get("episodes", []) if e["job_id"] == job_id), None)

    def next_episode_number(self) -> int:
        return max((int(e["number"]) for e in self.data.get("episodes", [])), default=0) + 1


class ComicSeriesStore:
    """Series of one owner live under ``series_root(owner)``."""

    def __init__(self, series_root: Callable[[str], Path]) -> None:
        self.series_root = series_root

    # ------------------------------------------------------------- storage
    def _dir(self, owner: str, series_id: str) -> Path | None:
        if not _SERIES_ID.fullmatch(series_id or ""):
            return None
        return self.series_root(owner) / series_id

    def load(self, owner: str, series_id: str) -> ComicSeries | None:
        directory = self._dir(owner, series_id)
        if directory is None:
            return None
        try:
            data = json.loads((directory / "series.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return ComicSeries(directory, data) if isinstance(data, dict) else None

    def save(self, series: ComicSeries) -> ComicSeries:
        series.data["updated_at"] = _now()
        write_json_atomic(series.root / "series.json", series.data)
        return series

    def create(self, owner: str, *, title: str, description: str, settings: dict[str, Any]) -> ComicSeries:
        series_id = uuid.uuid4().hex[:12]
        now = _now()
        series = ComicSeries(self.series_root(owner) / series_id, {
            "id": series_id, "title": title, "description": description, "settings": settings,
            "characters": [], "episodes": [], "created_at": now, "updated_at": now,
        })
        with _LOCK:
            return self.save(series)

    def list(self, owner: str) -> list[ComicSeries]:
        root = self.series_root(owner)
        found = [self.load(owner, path.name) for path in root.iterdir() if path.is_dir()] if root.is_dir() else []
        return sorted((s for s in found if s is not None), key=lambda s: s.data.get("updated_at", ""), reverse=True)

    def delete(self, owner: str, series_id: str) -> bool:
        directory = self._dir(owner, series_id)
        if directory is None or not (directory / "series.json").is_file():
            return False
        shutil.rmtree(directory, ignore_errors=True)
        return True

    def update(self, owner: str, series_id: str, change: Callable[[ComicSeries], None]) -> ComicSeries | None:
        with _LOCK:
            series = self.load(owner, series_id)
            if series is None:
                return None
            change(series)
            return self.save(series)

    def add_episode(self, owner: str, series_id: str, job_id: str) -> int:
        number = 0

        def change(series: ComicSeries) -> None:
            nonlocal number
            number = series.next_episode_number()
            series.data.setdefault("episodes", []).append({"job_id": job_id, "number": number, "created_at": _now()})

        if self.update(owner, series_id, change) is None:
            raise KeyError(series_id)
        return number

    def remove_episode(self, owner: str, series_id: str, job_id: str) -> None:
        def change(series: ComicSeries) -> None:
            series.data["episodes"] = [e for e in series.data.get("episodes", []) if e["job_id"] != job_id]

        self.update(owner, series_id, change)

    # ----------------------------------------------------- episode exchange
    def known_characters(self, owner: str, series_id: str) -> list[ComicCharacter]:
        series = self.load(owner, series_id)
        return series.characters if series else []

    def known_locations(self, owner: str, series_id: str) -> list[ComicLocation]:
        series = self.load(owner, series_id)
        return series.locations if series else []

    def known_world(self, owner: str, series_id: str) -> str:
        series = self.load(owner, series_id)
        return series.world if series else ""

    def known_world_vi(self, owner: str, series_id: str) -> str:
        series = self.load(owner, series_id)
        return str(series.data.get("world_vi") or "") if series else ""

    def sync_into_episode(self, owner: str, series_id: str, project: ComicProject, storyboard: Storyboard) -> Storyboard:
        """Pin the series' approved references on the episode's characters.

        A reference the user pinned inside the episode itself (a file the series does
        not hold) wins; otherwise the episode follows the series' current approval.
        """
        series = self.load(owner, series_id)
        if series is None:
            return storyboard
        bible = {c.id: c for c in series.characters}
        changed = False
        for character in storyboard.characters:
            shared = bible.get(character.id)
            source = series.ref_path(shared.approved_reference if shared else None)
            own = character.approved_reference
            if source is None or own == source.name or (own and series.ref_path(own) is None):
                continue
            target = project.dir("refs") / source.name
            if not target.is_file():
                shutil.copy2(source, target)
            character.approved_reference = source.name
            changed = True
        shared_places = {place.id: place for place in series.locations}
        for place in storyboard.locations:
            shared = shared_places.get(place.id)
            source = series.ref_path(shared.approved_reference if shared else None)
            own = place.approved_reference
            if source is None or own == source.name or (own and series.ref_path(own) is None):
                continue
            target = project.dir("refs") / source.name
            if not target.is_file():
                shutil.copy2(source, target)
            place.approved_reference = source.name
            changed = True
        return project.save_storyboard(storyboard) if changed else storyboard

    def absorb_episode(self, owner: str, series_id: str, project: ComicProject, storyboard: Storyboard,
                       manifest: dict[str, Any] | None = None) -> None:
        """Add the episode's new characters, places and first references to the series bible."""
        produced = (manifest or {}).get("references", {})

        def change(series: ComicSeries) -> None:
            bible = series.characters
            by_id = {c.id: c for c in bible}
            for character in storyboard.characters:
                shared = by_id.get(character.id)
                if shared is None:
                    shared = character.model_copy(update={"approved_reference": None})
                    bible.append(shared)
                    by_id[shared.id] = shared
                for english in ("appearance", "outfit"):  # Fill a translation the series still lacks.
                    if not getattr(shared, english) and getattr(character, english):
                        setattr(shared, english, getattr(character, english))
                if series.ref_path(shared.approved_reference) is not None:
                    continue
                name = character.approved_reference or produced.get(character.id)
                source = project.asset_path("refs", name or "")
                if source is None:
                    continue
                if not (series.refs_dir / source.name).is_file():
                    shutil.copy2(source, series.refs_dir / source.name)
                shared.approved_reference = source.name
            series.set_characters(bible)
            places = series.locations
            known_places = {place.id: place for place in places}
            # Copies: the series must never edit the episode's own storyboard objects.
            places += [place.model_copy() for place in storyboard.locations if place.id not in known_places]
            for place in storyboard.locations:  # An English translation the series still lacks.
                shared = known_places.get(place.id)
                if shared is not None and not shared.description and place.description:
                    shared.description = place.description
            # The first picture drawn (or pinned) for a place becomes the series' picture of it.
            drawn = (manifest or {}).get("location_references", {})
            by_place = {place.id: place for place in places}
            for place in storyboard.locations:
                shared = by_place.get(place.id)
                if shared is None or series.ref_path(shared.approved_reference) is not None:
                    continue
                source = project.asset_path("refs", place.approved_reference or drawn.get(place.id) or "")
                if source is None:
                    continue
                if not (series.refs_dir / source.name).is_file():
                    shutil.copy2(source, series.refs_dir / source.name)
                shared.approved_reference = source.name
            series.set_locations(places)
            if storyboard.world and not series.world:
                series.data["world"] = storyboard.world
            if storyboard.world_vi and not series.data.get("world_vi"):
                series.data["world_vi"] = storyboard.world_vi

        self.update(owner, series_id, change)
