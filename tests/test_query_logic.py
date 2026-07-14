from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from src import query_logic


class QueryDatabase:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY,
                project_id INTEGER,
                file_id INTEGER,
                type TEXT,
                name TEXT,
                start_line INTEGER,
                end_line INTEGER
            );
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY,
                source_id INTEGER,
                target_id INTEGER,
                type TEXT
            );
            """
        )

    def get_node_by_name(self, project_id: int, name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM nodes WHERE project_id = ? AND name = ?",
            (project_id, name),
        ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self.conn.close()


@pytest.fixture
def db(tmp_path: Path) -> Iterator[QueryDatabase]:
    database = QueryDatabase()
    source = tmp_path / "module.py"
    source.write_text("x" * 400, encoding="utf-8")
    database.conn.execute("INSERT INTO files VALUES (1, ?)", (str(source),))
    database.conn.executemany(
        "INSERT INTO nodes VALUES (?, 1, 1, 'function', ?, 1, 2)",
        [(1, "caller"), (2, "callee")],
    )
    database.conn.execute("INSERT INTO edges VALUES (1, 1, 2, 'calls')")
    database.conn.commit()
    yield database
    database.close()


def test_dependency_queries_cover_both_directions_and_empty_results(
    db: QueryDatabase,
) -> None:
    dependencies, _, saved = query_logic.do_find_deps(db, 1, "caller", False)
    dependents, _, _ = query_logic.do_find_deps(db, 1, "callee", True)
    no_edges, _, _ = query_logic.do_find_deps(db, 1, "callee", False)
    missing, _, _ = query_logic.do_find_deps(db, 1, "missing", False)

    assert (
        dependencies == "Dependencies for 'caller':\n- callee (function) [edge: calls]"
    )
    assert dependents == "Dependents for 'callee':\n- caller (function) [edge: calls]"
    assert no_edges == "No dependencies found for 'callee'."
    assert missing == "Symbol 'missing' not found."
    assert saved > 0


def test_cosine_similarity_handles_normal_and_zero_vectors() -> None:
    assert query_logic.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert query_logic.cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert query_logic.cosine_similarity([], []) == 0.0


def test_semantic_search_ranks_results_and_handles_empty_or_unready_storage(
    db: QueryDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Model:
        def encode(self, query: str):
            assert query == "needle"

            class Encoded:
                def tolist(self) -> list[float]:
                    return [1.0, 0.0]

            return Encoded()

    monkeypatch.setattr(query_logic, "get_embed_model", lambda: Model())
    unready, _, unready_tokens = query_logic.do_semantic_search(db, 1, "needle")
    assert unready == "Semantic search is not ready for this database."
    assert unready_tokens == 0

    db.conn.execute("CREATE TABLE embeddings (node_id INTEGER, vector TEXT)")
    empty, _, _ = query_logic.do_semantic_search(db, 1, "needle")
    assert empty == "No semantic matches found for 'needle'."

    db.conn.executemany(
        "INSERT INTO embeddings VALUES (?, ?)",
        [(1, json.dumps([0.5, 0.5])), (2, json.dumps([1.0, 0.0]))],
    )
    db.conn.commit()
    ranked, _, tokens = query_logic.do_semantic_search(db, 1, "needle", limit=1)

    assert "callee" in ranked
    assert "caller" not in ranked
    assert tokens > 0


def test_semantic_readiness_is_checked_before_model_loading(
    db: QueryDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_loads = 0

    def fail_if_loaded() -> object:
        nonlocal model_loads
        model_loads += 1
        raise AssertionError("model loading must follow storage readiness")

    monkeypatch.setattr(query_logic, "get_embed_model", fail_if_loaded)

    unready, _, _ = query_logic.do_semantic_search(db, 1, "needle")
    db.conn.execute("CREATE TABLE embeddings (node_id INTEGER, vector TEXT)")
    empty, _, _ = query_logic.do_semantic_search(db, 1, "needle")

    assert unready == "Semantic search is not ready for this database."
    assert empty == "No semantic matches found for 'needle'."
    assert model_loads == 0


def test_embed_model_uses_pinned_local_only_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    fake_module = ModuleType("sentence_transformers")

    class FakeSentenceTransformer:
        def __init__(self, model_name: str, **kwargs: object) -> None:
            observed["model_name"] = model_name
            observed.update(kwargs)

    fake_module.SentenceTransformer = FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(query_logic, "_embed_model", None)

    first = query_logic.get_embed_model()
    second = query_logic.get_embed_model()

    assert first is second
    assert observed == {
        "model_name": "BAAI/bge-small-en-v1.5",
        "revision": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        "local_files_only": True,
        "trust_remote_code": False,
    }


def test_semantic_search_skips_corrupt_or_incompatible_vectors(
    db: QueryDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Model:
        def encode(self, _query: str):
            return type("Encoded", (), {"tolist": lambda self: [1.0, 0.0]})()

    monkeypatch.setattr(query_logic, "get_embed_model", lambda: Model())
    db.conn.execute("CREATE TABLE embeddings (node_id INTEGER, vector TEXT)")
    db.conn.executemany(
        "INSERT INTO embeddings VALUES (?, ?)",
        [(1, "not-json"), (2, json.dumps([1.0]))],
    )
    db.conn.commit()

    result, _, _ = query_logic.do_semantic_search(db, 1, "needle")

    assert result == "No semantic matches found for 'needle'."
