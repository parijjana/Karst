from __future__ import annotations

import ast
import importlib
from pathlib import Path


CORE_MODULES = (
    "src.karst_core.parser",
    "src.karst_core.parser.discovery",
    "src.karst_core.parser.facade",
    "src.karst_core.parser.models",
    "src.karst_core.parser.runtime",
    "src.karst_core.parser.snapshots",
    "src.karst_core.parser.symbols",
    "src.karst_core.indexing",
    "src.karst_core.indexing.generation_service",
    "src.karst_core.indexing.identity",
    "src.karst_core.indexing.models",
    "src.karst_core.indexing.plan",
    "src.karst_core.indexing.repository",
    "src.karst_core.indexing.service",
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


def test_core_parser_and_indexing_modules_are_importable() -> None:
    for module_name in CORE_MODULES:
        importlib.import_module(module_name)


def test_legacy_facades_preserve_core_public_types() -> None:
    legacy_parser = importlib.import_module("src.parser")
    core_parser = importlib.import_module("src.karst_core.parser")
    legacy_indexing = importlib.import_module("src.indexing_service")
    core_indexing = importlib.import_module("src.karst_core.indexing")

    assert legacy_parser.CodeParser is core_parser.CodeParser
    assert legacy_parser.ParseOutcome is core_parser.ParseOutcome
    assert legacy_indexing.ProjectIndexService is core_indexing.ProjectIndexService
    assert legacy_indexing.IndexResult is core_indexing.IndexResult


def test_core_has_no_mission_control_or_web_process_dependencies() -> None:
    forbidden = (
        "fastapi",
        "uvicorn",
        "src.mission_control_transition",
        "src.web",
        "src.web_auth",
        "src.web_data",
        "src.web_graph",
        "src.web_history",
        "src.web_sessions",
    )
    core_root = Path("src/karst_core")
    for path in core_root.rglob("*.py"):
        for imported in _imports(path):
            assert not any(
                imported == prefix or imported.startswith(f"{prefix}.")
                for prefix in forbidden
            ), f"{path} imports forbidden dependency {imported}"


def test_main_uses_core_parser_and_indexing_namespaces() -> None:
    imported = _imports(Path("src/main.py"))

    assert "src.karst_core.parser" in imported
    assert "src.karst_core.indexing.service" in imported
    assert "src.parser" not in imported
    assert "src.indexing_service" not in imported
