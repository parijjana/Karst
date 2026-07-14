"""Deterministic, side-effect-free manifest comparison."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from src.index_identity import FileCandidate, SourceSnapshot


@dataclass(frozen=True, slots=True)
class ManifestRecord:
    candidate: FileCandidate
    content_sha256: str
    byte_size: int

    @classmethod
    def from_snapshot(cls, snapshot: SourceSnapshot) -> "ManifestRecord":
        return cls(snapshot.candidate, snapshot.content_sha256, snapshot.byte_size)


class PlanAction(str, Enum):
    UNCHANGED = "unchanged"
    PARSE = "parse"
    DELETE = "delete"
    RENAME = "rename"


@dataclass(frozen=True, slots=True)
class PlanItem:
    action: PlanAction
    current: ManifestRecord | None
    previous: ManifestRecord | None = None


@dataclass(frozen=True, slots=True)
class IndexCounts:
    unchanged: int = 0
    parse: int = 0
    deleted: int = 0
    renamed: int = 0


@dataclass(frozen=True, slots=True)
class IndexPlan:
    items: tuple[PlanItem, ...]
    counts: IndexCounts
    diagnostics: tuple[str, ...] = ()


def build_manifest_plan(
    current: tuple[ManifestRecord, ...] | list[ManifestRecord],
    previous: tuple[ManifestRecord, ...] | list[ManifestRecord] = (),
) -> IndexPlan:
    now = {r.candidate.relative_path: r for r in current}
    old = {r.candidate.relative_path: r for r in previous}
    items: list[PlanItem] = []
    diagnostics: list[str] = []
    consumed: set[str] = set()
    for path in sorted(now):
        record = now[path]
        prior = old.get(path)
        if prior and (prior.content_sha256, prior.byte_size) == (record.content_sha256, record.byte_size):
            items.append(PlanItem(PlanAction.UNCHANGED, record, prior))
            consumed.add(path)
            continue
        matches = [r for p, r in old.items() if p not in now and p not in consumed and (r.content_sha256, r.byte_size) == (record.content_sha256, record.byte_size)]
        if len(matches) == 1:
            renamed = ManifestRecord(FileCandidate.for_content_preserving_rename(matches[0].candidate, path), record.content_sha256, record.byte_size)
            items.append(PlanItem(PlanAction.RENAME, renamed, matches[0]))
            consumed.add(matches[0].candidate.relative_path)
        else:
            if len(matches) > 1:
                diagnostics.append("ambiguous_content_rename")
            items.append(PlanItem(PlanAction.PARSE, record, prior))
    for path in sorted(set(old) - set(now) - consumed):
        items.append(PlanItem(PlanAction.DELETE, None, old[path]))
    counts = IndexCounts(sum(i.action is PlanAction.UNCHANGED for i in items), sum(i.action is PlanAction.PARSE for i in items), sum(i.action is PlanAction.DELETE for i in items), sum(i.action is PlanAction.RENAME for i in items))
    return IndexPlan(tuple(items), counts, tuple(diagnostics))


manifest_plan = build_manifest_plan
