from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from scripts import embedder
from scripts.embedder import EmbeddingRecord, pending_node_ids, store_embedding_batch
from src.karst_core.embeddings import get_node_text
from src.karst_core.embeddings import model as embedding_model
from src.karst_core.database.database import Database
from src.settings import TRUSTED_LOCAL_OWNER


def test_embedder_uses_migrated_schema_and_idempotent_batch_storage(
    tmp_path: Path,
) -> None:
    with Database(tmp_path / "embedder.db") as database:
        project_id = database.add_project(
            "project",
            "/project",
            TRUSTED_LOCAL_OWNER,
            "stable:project",
        )
        file_id = database.add_file(project_id, "/project/a.py", "hash")
        node_id = database.add_node(project_id, file_id, "function", "run", 1, 2)

        assert pending_node_ids(database) == (node_id,)
        store_embedding_batch(
            database,
            (
                EmbeddingRecord(
                    node_id,
                    "[0.1]",
                    content_hash="first",
                    model_revision="model@1",
                ),
            ),
        )
        store_embedding_batch(
            database,
            (
                EmbeddingRecord(
                    node_id,
                    "[0.2]",
                    content_hash="second",
                    model_revision="model@1",
                ),
            ),
        )

        assert pending_node_ids(database) == ()
        assert (
            database.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 1
        )
        assert tuple(
            database.conn.execute(
                "SELECT vector, content_hash, model_revision FROM embeddings"
            ).fetchone()
        ) == ("[0.2]", "second", "model@1")


def test_embedder_model_is_pinned_and_never_downloaded(
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

    embedder.get_embed_model()

    assert observed == {
        "model_name": embedder.MODEL_NAME,
        "revision": embedder.MODEL_REVISION,
        "local_files_only": True,
        "trust_remote_code": False,
    }


def test_core_model_cache_reuses_the_pinned_local_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = object()
    monkeypatch.setattr(embedding_model, "_embed_model", None)
    monkeypatch.setattr(embedding_model, "create_embed_model", lambda: loaded)

    assert embedding_model.get_embed_model() is loaded
    assert embedding_model.get_embed_model() is loaded


def test_core_repository_builds_node_text_and_handles_missing_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "module.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    with Database(tmp_path / "node-text.db") as database:
        project_id = database.add_project(
            "project",
            str(tmp_path),
            TRUSTED_LOCAL_OWNER,
            "stable:project",
        )
        file_id = database.add_file(project_id, str(source), "hash")
        node_id = database.add_node(project_id, file_id, "function", "run", 1, 2)

        assert get_node_text(database, node_id) == (
            "function run\ndef run():\n    return 1\n"
        )
        source.unlink()
        assert get_node_text(database, node_id) == "function run"
        assert get_node_text(database, node_id + 1) == ""
