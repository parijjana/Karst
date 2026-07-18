from __future__ import annotations

import ast
import importlib
from pathlib import Path


CORE_MODULES = (
    "src.karst_core.git_history",
    "src.karst_core.git_history.ingestion",
    "src.karst_core.embeddings",
    "src.karst_core.embeddings.model",
    "src.karst_core.embeddings.repository",
    "src.karst_core.embeddings.search",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def test_history_and_embedding_core_modules_are_importable() -> None:
    for module_name in CORE_MODULES:
        importlib.import_module(module_name)


def test_legacy_history_and_embedder_facades_preserve_public_objects() -> None:
    legacy_history = importlib.import_module("src.git_logic")
    core_history = importlib.import_module("src.karst_core.git_history")
    legacy_embedder = importlib.import_module("scripts.embedder")
    core_embeddings = importlib.import_module("src.karst_core.embeddings")

    assert (
        legacy_history.do_backfill_git_history is core_history.do_backfill_git_history
    )
    assert legacy_history.GIT_TIMEOUT_SECONDS == core_history.GIT_TIMEOUT_SECONDS
    assert legacy_history.MAX_HISTORY_LIMIT == core_history.MAX_HISTORY_LIMIT
    assert legacy_embedder.EmbeddingRecord is core_embeddings.EmbeddingRecord
    assert legacy_embedder.pending_node_ids is core_embeddings.pending_node_ids
    assert (
        legacy_embedder.store_embedding_batch is core_embeddings.store_embedding_batch
    )


def test_main_uses_core_history_and_embedding_namespaces() -> None:
    imported = _imports(Path("src/main.py"))

    assert "src.karst_core.git_history" in imported
    assert "src.karst_core.embeddings" in imported
    assert "src.git_logic" not in imported
    assert "src.query_logic" not in imported


def test_embedder_keeps_orchestration_outside_core() -> None:
    script_imports = _imports(Path("scripts/embedder.py"))
    assert "src.karst_core.embeddings" in script_imports

    forbidden = (
        "apscheduler",
        "asyncio",
        "scripts",
        "src.mission_control_transition",
        "threading",
    )
    for package in (
        Path("src/karst_core/git_history"),
        Path("src/karst_core/embeddings"),
    ):
        for path in package.rglob("*.py"):
            for imported in _imports(path):
                assert not any(
                    imported == prefix or imported.startswith(f"{prefix}.")
                    for prefix in forbidden
                ), f"{path} imports orchestration dependency {imported}"


def test_embedder_remains_an_explicit_process_entry_point() -> None:
    tree = ast.parse(Path("scripts/embedder.py").read_text(encoding="utf-8"))
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}

    assert "main" in functions
