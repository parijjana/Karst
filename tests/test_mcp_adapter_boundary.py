from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Protocol

import pytest

from src import main


EXPECTED_TOOL_CONTRACT_HASHES = {
    "backfill_git_history": "3b500c35539fef54fd87d3b5310d703ac332a1e7109c6f19510ac852fcd4d0d7",
    "find_dependencies": "43bcea3c479611441f50575325e40034b2a4054bf5a99daf290ee8beeefff4ce",
    "find_dependents": "e67414b0d4da543dc76d2196640cb4cbdb8414e6298a86fa441970434300fead",
    "get_file_outline": "34e38a75ff3b7187b85c28f0553e37ffb8a7357b821955f2ae39cdff08662c06",
    "index_project": "9ba37f35cd67a5dfa638e1d8ee7b1c02a9ab7b27f145165e98bbaf212aef768a",
    "list_symbols": "0508b1b65dd93f3ef6f9bcda9eedf6188a20bda60766e46fdc1d5f578b05f49e",
    "log_commit": "2b661b9ca1bc8cf91360af5c2af9def4364a1d61a19a4b27a8f93ce1d7ff01f3",
    "query_symbol": "cd8d3d537b1fd09cd63e8633c255e3b47baee6e76c3063a1103bd9850bd935c8",
    "rebuild_database": "042240ff135ac3933708bf16f3363d6056db74dada8c764e0d474303bee302d4",
    "semantic_search": "c751c355c9be27a0f0558394fda149d28bdb0afdcc86a24a93c749fcee7910bd",
    "update_graph": "a98ed943175b4aead7a8a98b145c3c348bf442d90989cc41590974b5655b43ec",
}


class RegisteredToolSchema(Protocol):
    @property
    def description(self) -> object: ...

    @property
    def parameters(self) -> object: ...

    @property
    def output_schema(self) -> object: ...


def _contract_hash(tool: RegisteredToolSchema) -> str:
    payload = {
        "description": tool.description,
        "parameters": tool.parameters,
        "output_schema": tool.output_schema,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _forbidden_adapter_imports(source: str) -> list[str]:
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imported_modules.add(node.module)
            elif node.level == 1:
                continue
            elif node.level == 2:
                if node.module:
                    imported_modules.add(f"src.{node.module}")
                else:
                    imported_modules.update(f"src.{alias.name}" for alias in node.names)
            else:
                imported_modules.add(f"{'.' * node.level}{node.module or ''}")

    allowed_prefixes = ("mcp", "src.karst_core", "src.karst_mcp")
    return sorted(
        module
        for module in imported_modules
        if module.partition(".")[0] not in sys.stdlib_module_names
        and not any(
            module == prefix or module.startswith(prefix + ".")
            for prefix in allowed_prefixes
        )
    )


def test_main_composes_mcp_through_the_transport_adapter() -> None:
    tree = ast.parse(Path("src/main.py").read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    decorated_functions = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.decorator_list
    ]
    functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "mcp.server.fastmcp" not in imports
    assert "src.karst_mcp.contracts" in imports
    assert "src.karst_mcp.server" in imports
    assert decorated_functions == []
    assert functions.isdisjoint(EXPECTED_TOOL_CONTRACT_HASHES)


def test_structured_contracts_register_the_exact_public_tool_surface() -> None:
    from src.karst_mcp.contracts import ToolContract

    registered = main.mcp._tool_manager._tools

    assert all(isinstance(contract, ToolContract) for contract in main.TOOL_CONTRACTS)
    assert {contract.name for contract in main.TOOL_CONTRACTS} == set(
        EXPECTED_TOOL_CONTRACT_HASHES
    )
    assert {
        name: _contract_hash(tool) for name, tool in registered.items()
    } == EXPECTED_TOOL_CONTRACT_HASHES


def test_data_core_import_does_not_load_transport_web_or_operations() -> None:
    script = """
import sys
import src.karst_core

forbidden = ("mcp", "fastapi", "uvicorn", "src.web", "src.mission_control_transition")
loaded = sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
)
if loaded:
    raise SystemExit("forbidden modules loaded: " + ", ".join(loaded))
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path.cwd(),
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_mcp_adapter_imports_only_its_own_package_and_data_core() -> None:
    adapter_root = Path("src/karst_mcp")
    forbidden: dict[str, list[str]] = {}

    for source in adapter_root.glob("*.py"):
        imports = _forbidden_adapter_imports(source.read_text(encoding="utf-8"))
        if imports:
            forbidden[source.as_posix()] = imports

    assert forbidden == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import src.web", ["src.web"]),
        ("from ..web import routes", ["src.web"]),
        ("import fastapi\nfrom uvicorn import Config", ["fastapi", "uvicorn"]),
        (
            "from apscheduler.schedulers import background\nimport psutil",
            ["apscheduler.schedulers", "psutil"],
        ),
    ],
)
def test_mcp_adapter_import_guard_rejects_runtime_boundary_bypasses(
    source: str, expected: list[str]
) -> None:
    assert _forbidden_adapter_imports(source) == expected


def test_mcp_adapter_import_guard_allows_declared_dependencies() -> None:
    source = """
from __future__ import annotations
from collections.abc import Callable
import secrets
from mcp.server.fastmcp import FastMCP
from src.karst_core.query import QueryService
from src.karst_mcp.contracts import ToolContract
from . import contracts
"""

    assert _forbidden_adapter_imports(source) == []
