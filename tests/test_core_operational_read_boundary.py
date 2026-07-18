"""Architecture boundary for dashboard/admin read ownership."""

from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest


WEB_MODULES = tuple(sorted(Path("src").glob("web*.py")))
FORBIDDEN_HELPERS = {"get_db", "table_exists"}
FORBIDDEN_ATTRIBUTES = {"conn", "cursor", "execute", "executemany", "executescript"}
SQL_PREFIX = re.compile(
    r"^\s*(?:SELECT|WITH|PRAGMA|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)\b", re.I
)


def _is_database_module(name: str) -> bool:
    return name in {
        "sqlite3",
        "database",
        "database_session",
        "src.database",
        "src.database_session",
    } or name.startswith("src.karst_core.database")


def _resolved_from_module(node: ast.ImportFrom, path: str) -> str:
    if node.level == 0:
        return node.module or ""
    package = list(Path(path.replace("\\", "/")).with_suffix("").parts[:-1])
    parent_hops = node.level - 1
    if parent_hops:
        package = package[:-parent_hops] if parent_hops <= len(package) else []
    return ".".join((*package, *((node.module or "").split("."))))


def _database_violations(source: str, path: str = "route.py") -> list[str]:
    tree = ast.parse(source, filename=path)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name for alias in node.names}
            if any(_is_database_module(name) for name in names):
                violations.append(f"{path}:{node.lineno}:database import")
        if isinstance(node, ast.ImportFrom):
            module = _resolved_from_module(node, path)
            names = {alias.name for alias in node.names}
            imported_modules = {module, *(f"{module}.{name}" for name in names)}
            if any(_is_database_module(name) for name in imported_modules) or bool(
                names & {"Database", "get_db", "database_session"}
            ):
                violations.append(f"{path}:{node.lineno}:database import")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name in FORBIDDEN_HELPERS
        ):
            violations.append(f"{path}:{node.lineno}:helper {node.name}")
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            violations.append(f"{path}:{node.lineno}:direct .{node.attr}")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_HELPERS:
            violations.append(f"{path}:{node.lineno}:helper reference {node.id}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if SQL_PREFIX.match(node.value):
                violations.append(f"{path}:{node.lineno}:SQL literal")
    return violations


def test_web_modules_do_not_own_database_sessions_or_sql() -> None:
    violations: list[str] = []
    for path in WEB_MODULES:
        violations.extend(
            _database_violations(path.read_text(encoding="utf-8"), str(path))
        )
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "path"),
    [
        ("import sqlite3 as storage", "route.py"),
        ("import src.karst_core.database.database_session as session", "route.py"),
        ("from src.karst_core.database.database import Database as Store", "route.py"),
        ("from database_session import get_db as acquire", "route.py"),
        ("from src.karst_core import database as storage", "route.py"),
        ("from .karst_core import database as storage", "src/web_data.py"),
        ("from . import database_session as storage", "src/web_data.py"),
        ("from ..karst_core import database as storage", "src/web/routes.py"),
        (
            "def route(db):\n    return db.conn.cursor().execute('SELECT 1')",
            "route.py",
        ),
        (
            "def route(db):\n    return db.executemany('INSERT INTO t VALUES (?)', [])",
            "route.py",
        ),
        ("QUERY = '''WITH rows AS (SELECT 1) SELECT * FROM rows'''", "route.py"),
    ],
)
def test_boundary_detects_database_ownership_bypasses(source: str, path: str) -> None:
    assert _database_violations(source, path)
