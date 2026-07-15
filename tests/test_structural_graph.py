from __future__ import annotations

from pathlib import Path

from src.core_settings import TRUSTED_LOCAL_OWNER
from src.karst_core.database.database import Database
from src.karst_core.structural_graph import StructuralGraphService


def _weight(node: dict[str, object]) -> int:
    weight = node["weight"]
    assert isinstance(weight, int)
    return weight


def _detail_value(node: dict[str, object], key: str) -> object | None:
    detail = node.get("detail")
    if not isinstance(detail, dict):
        return None
    return detail.get(key)


def _ready_graph(path: Path) -> None:
    with Database(path) as database:
        project_id = database.add_project(
            "demo", str(path.parent), TRUSTED_LOCAL_OWNER, "project:demo"
        )
        root_file = database.add_file(project_id, str(path.parent / "root.py"), "a")
        nested_file = database.add_file(
            project_id, str(path.parent / "pkg" / "child.py"), "b"
        )
        database.add_node(project_id, root_file, "class", "HiddenClass", 1, 2)
        database.add_node(project_id, nested_file, "function", "hidden_fn", 3, 4)
        generation_id = int(
            database.conn.execute(
                "SELECT id FROM index_generations WHERE project_id=?", (project_id,)
            ).fetchone()[0]
        )
        database.conn.execute(
            "UPDATE index_generations SET query_ready=1, manifest_sha256=? WHERE id=?",
            ("a" * 64, generation_id),
        )


def test_structural_graph_contains_hierarchy_weights_and_colored_code_edges(
    tmp_path: Path,
) -> None:
    path = tmp_path / "graph.db"
    _ready_graph(path)

    graph = StructuralGraphService(path).graph().as_dict()
    nodes = {str(node["id"]): node for node in graph["nodes"]}

    assert "karst" in nodes
    projects = [node for node in nodes.values() if node["type"] == "project"]
    folders = [node for node in nodes.values() if node["type"] == "folder"]
    files = [node for node in nodes.values() if node["type"] == "file"]
    dots = [node for node in nodes.values() if node["type"] == "code_dot"]
    assert len(projects) == 1 and projects[0]["weight"] == 2
    assert len(folders) == 1 and folders[0]["weight"] == 1
    assert sorted(_weight(node) for node in files) == [1, 1]
    assert len(dots) == 2
    assert all("name" not in node and "detail" not in node for node in dots)
    code_edges = [link for link in graph["links"] if link["type"] == "code_node"]
    assert {edge["node_type"] for edge in code_edges} == {"class", "function"}
    assert all(link["type"] == "structural" for link in graph["links"] if link not in code_edges)
    assert any(_detail_value(node, "path") == "pkg/child.py" for node in files)


def test_structural_graph_ids_are_stable_and_excludes_non_query_ready_generations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "graph.db"
    _ready_graph(path)

    first = StructuralGraphService(path).graph().as_dict()
    second = StructuralGraphService(path).graph().as_dict()
    assert [node["id"] for node in first["nodes"]] == [node["id"] for node in second["nodes"]]

    with Database(path) as database:
        project_id = database.add_project(
            "unfinished", str(tmp_path), TRUSTED_LOCAL_OWNER, "project:unfinished"
        )
        database.add_file(project_id, str(tmp_path / "unfinished.py"), "c")
    graph = StructuralGraphService(path).graph().as_dict()
    assert all(
        _detail_value(node, "name") != "unfinished" for node in graph["nodes"]
    )
