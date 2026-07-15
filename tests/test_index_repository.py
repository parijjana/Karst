from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from src.karst_core.database.database import Database
from src.index_identity import FileCandidate, SourceSnapshot
from src.index_models import ParseStatus, ParsedFile, ParsedSymbol
from src.index_identity import derive_symbol_stable_id
from src.index_repository import IndexRepository
from tests.database_v3_contract_support import add_project


PROJECT = str(uuid5(NAMESPACE_URL, "project:/repo"))


def test_generation_lifecycle_and_absolute_file_path(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as db:
        pid = add_project(db, "repo", PROJECT)
        repo = IndexRepository(db)
        generation = repo.admit(pid)
        candidate = FileCandidate.for_new_file(PROJECT, "src/a.py")
        repo.stage(generation.id, ParsedFile(SourceSnapshot(candidate, b"print(1)"), ParseStatus.INDEXED))
        row = db.conn.execute("SELECT path,relative_path,identity_path,hash FROM files WHERE generation_id=?", (generation.id,)).fetchone()
        assert Path(row[0]).is_absolute()
        assert tuple(row[1:3]) == ("src/a.py", "src/a.py")
        assert len(row[3]) == 64
        active = repo.promote(generation.id)
        assert active.query_ready
        with pytest.raises(ValueError):
            repo.promote(generation.id, query_ready=False)


def test_fail_and_cancel_persist_diagnostics(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as db:
        pid = add_project(db, "repo", PROJECT)
        repo = IndexRepository(db)
        failed = repo.admit(pid)
        repo.fail(failed.id)
        row = db.conn.execute("SELECT COUNT(*) FROM index_diagnostics WHERE generation_id=?", (failed.id,)).fetchone()
        assert row[0] == 1
        cancelled = repo.admit(pid)
        repo.cancel(cancelled.id)
        assert db.conn.execute("SELECT COUNT(*) FROM index_diagnostics WHERE generation_id=?", (cancelled.id,)).fetchone()[0] == 1


def test_clone_preserves_file_identity(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as db:
        pid = add_project(db, "repo", PROJECT)
        repo = IndexRepository(db)
        generation = repo.admit(pid)
        candidate = FileCandidate.for_new_file(PROJECT, "src/a.py")
        repo.stage(generation.id, ParsedFile(SourceSnapshot(candidate, b"x"), ParseStatus.INDEXED))
        repo.promote(generation.id)
        clone = repo.clone(pid)
        old = db.conn.execute("SELECT stable_id,identity_path FROM files WHERE generation_id=?", (generation.id,)).fetchone()
        new = db.conn.execute("SELECT stable_id,identity_path FROM files WHERE generation_id=?", (clone.id,)).fetchone()
        assert tuple(old) == tuple(new)


def test_stage_edges_rejects_integer_nodes_from_other_generation(tmp_path: Path) -> None:
    with Database(tmp_path / "db.sqlite") as db:
        pid = add_project(db, "repo", PROJECT)
        repo = IndexRepository(db)
        first = repo.admit(pid)
        candidate = FileCandidate.for_new_file(PROJECT, "src/a.py")
        symbol_id = derive_symbol_stable_id(candidate.stable_file_id, "python", "function", "a", None)
        parsed = ParsedFile(
            SourceSnapshot(candidate, b"x"),
            ParseStatus.INDEXED,
            symbols=(ParsedSymbol(symbol_id, candidate.stable_file_id, "python", "function", "a", "a", 1, 1),),
        )
        repo.stage(first.id, parsed)
        node_id = int(db.conn.execute("SELECT id FROM nodes WHERE generation_id=?", (first.id,)).fetchone()[0])
        repo.promote(first.id)
        second = repo.admit(pid)
        with pytest.raises(ValueError, match="another generation"):
            repo.stage_edges(second.id, ((node_id, node_id, "calls"),))
