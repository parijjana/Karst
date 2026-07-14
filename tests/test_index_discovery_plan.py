from pathlib import Path

from src.index_discovery import DiscoveryLimits, discover_snapshots
from src.index_identity import FileCandidate, SourceSnapshot
from src.index_plan import ManifestRecord, PlanAction, build_manifest_plan
from src.security import PathSecurityPolicy, stable_project_id
import pytest
from src.security import SecurityViolation


def test_discovery_is_sorted_and_snapshots_are_hashed(tmp_path: Path) -> None:
    (tmp_path / "b.dart").write_bytes(b"b")
    (tmp_path / "a.dart").write_bytes(b"a")
    policy = PathSecurityPolicy((tmp_path,))
    result = discover_snapshots(tmp_path, policy, limits=DiscoveryLimits(max_file_bytes=10, max_total_bytes=10))
    assert [item.candidate.relative_path for item in result.snapshots] == ["a.dart", "b.dart"]
    assert result.snapshots[0].byte_size == 1


def test_unique_rename_carries_identity_and_ambiguous_does_not() -> None:
    project = stable_project_id(Path("/tmp/project"))
    old_a = SourceSnapshot(FileCandidate.for_new_file(project, "a.dart"), b"same")
    old_b = SourceSnapshot(FileCandidate.for_new_file(project, "b.dart"), b"same")
    current = SourceSnapshot(FileCandidate.for_new_file(project, "c.dart"), b"same")
    ambiguous = build_manifest_plan(
        [ManifestRecord.from_snapshot(current)],
        [ManifestRecord.from_snapshot(old_a), ManifestRecord.from_snapshot(old_b)],
    )
    assert ambiguous.items[0].action is PlanAction.PARSE
    assert ambiguous.diagnostics == ("ambiguous_content_rename",)
    unique = build_manifest_plan(
        [ManifestRecord.from_snapshot(current)], [ManifestRecord.from_snapshot(old_a)]
    )
    assert unique.items[0].action is PlanAction.RENAME
    assert unique.items[0].current is not None
    assert unique.items[0].current.candidate.identity_path == "a.dart"


def test_discovery_rejects_budget_and_cancellation(tmp_path: Path) -> None:
    (tmp_path / "a.dart").write_bytes(b"1234")
    policy = PathSecurityPolicy((tmp_path,))
    with pytest.raises(SecurityViolation):
        discover_snapshots(tmp_path, policy, limits=DiscoveryLimits(max_file_bytes=2, max_total_bytes=2))
    with pytest.raises(SecurityViolation):
        discover_snapshots(tmp_path, policy, cancelled=lambda: True)


def test_plan_counts_changed_deleted_and_unchanged() -> None:
    project = stable_project_id(Path("/tmp/project2"))
    def record(path: str, content: bytes) -> ManifestRecord:
        return ManifestRecord.from_snapshot(SourceSnapshot(FileCandidate.for_new_file(project, path), content))
    plan = build_manifest_plan([record("same.dart", b"x"), record("new.dart", b"n")], [record("same.dart", b"x"), record("gone.dart", b"g")])
    assert plan.counts.unchanged == 1
    assert plan.counts.parse == 1
    assert plan.counts.deleted == 1
