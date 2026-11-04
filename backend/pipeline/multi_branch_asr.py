"""Multi-branch speech recognition: transcribe several versions of one soundtrack and fuse them.

Speech under a background song is recognised differently depending on the audio Whisper hears:

* ``original``  – the untouched mix. Keeps every spoken word but the song can leak in.
* ``separated`` – the vocal stem from a music separator (BS-RoFormer, Demucs fallback).
  Removes instruments, yet a singer stays in the stem together with the speaker.
* ``enhanced``  – the vocal stem after speech enhancement (DeepFilterNet3). Clearer
  dialogue, but the filter sometimes distorts words.

No branch is right everywhere, so nothing here assumes one is. Segments from all branches
are grouped into time clusters; where the branches agree the original is kept, where they
disagree the most confident reading wins (the original keeps a margin, because the stems
carry the singer too) and the cluster goes into a review report for a human to check.
"""

from __future__ import annotations

import difflib
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Sequence

PRIMARY_BRANCH = "original"
# Below this text similarity two branches "disagree" about a cluster.
AGREEMENT_THRESHOLD = 0.8
# An alternative replaces the original only when it is this much more confident.
SWITCH_MARGIN = 0.08
# Speech heard only by a stem is added when it is at least this confident.
ADDED_MIN_CONFIDENCE = 0.6
# The original alone, below this confidence, is kept but reported.
LOW_CONFIDENCE = 0.45
# Segments overlapping by less than this many seconds are separate utterances.
MIN_OVERLAP_SECONDS = 0.2
# An alternative must span this share of the original's time to replace it, so a reading
# cut short at a clip edge never swaps a whole line for half of it.
MIN_SWITCH_COVERAGE = 0.7

_PUNCTUATION = re.compile(r"[\W_]+", re.UNICODE)


@dataclass(frozen=True)
class BranchTranscript:
    name: str
    segments: Sequence[dict[str, Any]]


@dataclass
class FusionResult:
    segments: list[dict[str, Any]]
    review: list[dict[str, Any]] = field(default_factory=list)
    branches: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self.review:
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        return {
            "branches": self.branches,
            "segments": len(self.segments),
            "review_items": len(self.review),
            "by_kind": counts,
            "replaced": sum(1 for item in self.review if item.get("chosen") not in (None, PRIMARY_BRANCH)),
        }


def comparable_text(text: str) -> str:
    """Lower-case text without punctuation or spacing, so CJK and Latin compare alike."""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    return _PUNCTUATION.sub("", normalized)


def text_similarity(left: str, right: str) -> float:
    a, b = comparable_text(left), comparable_text(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def segment_confidence(segment: dict[str, Any]) -> float:
    """0..1 confidence: mean word probability, else exp(avg_logprob)."""
    words = [word for word in segment.get("words") or [] if word.get("probability") is not None]
    if words:
        return sum(float(word["probability"]) for word in words) / len(words)
    logprob = segment.get("avg_logprob")
    if logprob is None:
        return 0.5
    return max(0.0, min(1.0, math.exp(float(logprob))))


def _group_confidence(segments: Sequence[dict[str, Any]]) -> float:
    weighted = total = 0.0
    for segment in segments:
        duration = max(0.1, float(segment["end"]) - float(segment["start"]))
        weighted += segment_confidence(segment) * duration
        total += duration
    return weighted / total if total else 0.0


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    return min(float(a["end"]), float(b["end"])) - max(float(a["start"]), float(b["start"]))


def _clusters(branches: Sequence[BranchTranscript]) -> list[dict[str, list[dict[str, Any]]]]:
    """Group segments of all branches that overlap in time (sweep over start times)."""
    tagged = sorted(
        ((float(segment["start"]), float(segment["end"]), branch.name, segment)
         for branch in branches for segment in branch.segments if str(segment.get("text") or "").strip()),
        key=lambda item: (item[0], item[1]),
    )
    clusters: list[dict[str, list[dict[str, Any]]]] = []
    cluster_end = -math.inf
    for start, end, name, segment in tagged:
        if not clusters or start > cluster_end - MIN_OVERLAP_SECONDS:
            clusters.append({})
            cluster_end = end
        else:
            cluster_end = max(cluster_end, end)
        clusters[-1].setdefault(name, []).append(segment)
    return clusters


def _inside(spans: Sequence[tuple[float, float]], start: float, end: float) -> bool:
    middle = (start + end) / 2
    return any(span_start <= middle <= span_end for span_start, span_end in spans)


def _span(segments: Sequence[dict[str, Any]]) -> float:
    return max(float(s["end"]) for s in segments) - min(float(s["start"]) for s in segments)


def _candidate(name: str, segments: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "branch": name,
        "text": " ".join(str(segment["text"]).strip() for segment in segments),
        "confidence": round(_group_confidence(segments), 4),
    }


def fuse_branch_transcripts(
    branches: Sequence[BranchTranscript],
    *,
    music_spans: Sequence[tuple[float, float]] = (),
    checked_spans: Sequence[tuple[float, float]] | None = None,
    hallucination_spans: Sequence[tuple[float, float]] = (),
) -> FusionResult:
    """Return one transcript plus the clusters a human should check.

    ``music_spans`` are stretches the original pass deliberately ignored as music;
    speech that only a stem hears there is most likely the song's lyrics, so it is
    reported instead of added.

    ``checked_spans`` are the stretches the extra branches covered (None: the whole file);
    outside them only the original exists and nothing is reported. ``hallucination_spans``
    are original lines the classifier heard no voice under: when no stem hears anything
    there either and Whisper itself was unsure, all three signals agree and the line is dropped.
    """
    names = [branch.name for branch in branches]
    if PRIMARY_BRANCH not in names:
        raise ValueError("fusion needs the original branch")
    if len(branches) == 1:
        return FusionResult([dict(segment) for segment in branches[0].segments], [], names)

    fused: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for cluster in _clusters(branches):
        all_segments = [segment for segments in cluster.values() for segment in segments]
        start = min(float(segment["start"]) for segment in all_segments)
        end = max(float(segment["end"]) for segment in all_segments)
        candidates = {name: _candidate(name, segments) for name, segments in cluster.items()}
        primary = candidates.get(PRIMARY_BRANCH)
        report = {"start": round(start, 3), "end": round(end, 3), "candidates": list(candidates.values())}
        checked = checked_spans is None or _inside(checked_spans, start, end)

        if primary is None:
            best_name = max(candidates, key=lambda name: candidates[name]["confidence"])
            best = candidates[best_name]
            if _inside(music_spans, start, end):
                review.append({**report, "kind": "stem_only_in_music", "chosen": None})
            elif best["confidence"] >= ADDED_MIN_CONFIDENCE:
                fused.extend(_tag(cluster[best_name], best_name))
                review.append({**report, "kind": "added_from_stem", "chosen": best_name})
            else:
                review.append({**report, "kind": "stem_only_low_confidence", "chosen": None})
            continue

        others = {name: candidate for name, candidate in candidates.items() if name != PRIMARY_BRANCH}
        similarities = {name: text_similarity(primary["text"], other["text"]) for name, other in others.items()}
        report["similarity"] = {name: round(value, 3) for name, value in similarities.items()}
        disagreeing = {name: others[name] for name, value in similarities.items() if value < AGREEMENT_THRESHOLD}
        if not disagreeing:
            if not others and checked and primary["confidence"] < LOW_CONFIDENCE:
                if _inside(hallucination_spans, start, end):
                    review.append({**report, "kind": "dropped_possible_hallucination", "chosen": None})
                    continue
                review.append({**report, "kind": "original_only_low_confidence", "chosen": PRIMARY_BRANCH})
            fused.extend(_tag(cluster[PRIMARY_BRANCH], PRIMARY_BRANCH))
            continue
        best_name = max(disagreeing, key=lambda name: disagreeing[name]["confidence"])
        covers = _span(cluster[best_name]) >= MIN_SWITCH_COVERAGE * _span(cluster[PRIMARY_BRANCH])
        chosen = (
            best_name
            if covers and disagreeing[best_name]["confidence"] >= primary["confidence"] + SWITCH_MARGIN
            else PRIMARY_BRANCH
        )
        fused.extend(_tag(cluster[chosen], chosen))
        review.append({**report, "kind": "disagreement", "chosen": chosen})

    fused.sort(key=lambda segment: (float(segment["start"]), float(segment["end"])))
    return FusionResult(fused, review, names)


def _tag(segments: Sequence[dict[str, Any]], branch: str) -> list[dict[str, Any]]:
    return [{**segment, "asr_branch": branch} for segment in segments]
