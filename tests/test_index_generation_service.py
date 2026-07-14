from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from src.index_generation_service import IncrementalIndexService
from src.index_plan import ManifestRecord, PlanAction, build_manifest_plan
from src.index_identity import FileCandidate, SourceSnapshot


def test_incremental_plan_detects_unchanged_delete_and_rename() -> None:
    project = str(uuid5(NAMESPACE_URL, "project"))
    old = (ManifestRecord.from_snapshot(SourceSnapshot(FileCandidate.for_new_file(project, "a.py"), b"x")),)
    current = (ManifestRecord.from_snapshot(SourceSnapshot(FileCandidate.for_new_file(project, "b.py"), b"x")),)
    plan = build_manifest_plan(current, old)
    assert plan.counts.renamed == 1
    assert plan.items[0].action is PlanAction.RENAME


def test_cancelled_discovery_does_not_open_database(tmp_path: Path) -> None:
    called = False

    def factory():
        nonlocal called
        called = True
        raise AssertionError("database must not be opened")

    from src.security import PathSecurityPolicy
    service = IncrementalIndexService(factory, PathSecurityPolicy((tmp_path,)))
    result = service.index(1, tmp_path, cancel=lambda: True)
    assert result.status.value == "rejected"
    assert result.diagnostics[0].code == "index_cancelled"
    assert not called
