from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def _ancestor_ids(node: dict[str, object]) -> list[object]:
    ancestor_ids = node.get("ancestor_ids")
    return ancestor_ids if isinstance(ancestor_ids, list) else []


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


def test_structural_graph_marks_selected_folder_descendants_and_retains_context(
    tmp_path: Path,
) -> None:
    path = tmp_path / "graph.db"
    _ready_graph(path)
    service = StructuralGraphService(path)
    complete = service.graph().as_dict()
    folder = next(node for node in complete["nodes"] if node["type"] == "folder")
    folder_id = str(folder["id"])

    focused = service.graph(selected_folder_id=folder_id).as_dict()
    focused_nodes = {str(node["id"]): node for node in focused["nodes"]}

    assert focused["selected_folder_id"] == folder_id
    assert set(focused_nodes) == {str(node["id"]) for node in complete["nodes"]}
    assert {node["focus_state"] for node in focused_nodes.values()} == {
        "focus",
        "context",
    }
    assert focused_nodes[folder_id]["focus_state"] == "focus"

    nested_file = next(
        node
        for node in focused_nodes.values()
        if _detail_value(node, "path") == "pkg/child.py"
    )
    assert nested_file["focus_state"] == "focus"
    nested_dots = [
        node
        for node in focused_nodes.values()
        if node.get("parent_id") == nested_file["id"]
    ]
    assert nested_dots
    assert all(node["focus_state"] == "focus" for node in nested_dots)

    assert focused_nodes["karst"]["focus_state"] == "context"
    assert all(
        node["focus_state"] == "context"
        for node in focused_nodes.values()
        if node["type"] == "project"
        or _detail_value(node, "path") == "root.py"
    )
    serialized = json.dumps(focused)
    assert "HiddenClass" not in serialized
    assert "hidden_fn" not in serialized


def test_structural_graph_rejects_unknown_and_non_folder_selections(
    tmp_path: Path,
) -> None:
    path = tmp_path / "graph.db"
    _ready_graph(path)
    service = StructuralGraphService(path)
    complete = service.graph().as_dict()
    file_id = str(next(node["id"] for node in complete["nodes"] if node["type"] == "file"))

    for selected_id in ("folder_does_not_exist", file_id):
        with pytest.raises(ValueError, match="selected_folder_id"):
            service.graph(selected_folder_id=selected_id)


def test_structural_graph_rejects_folder_outside_requested_project_scope(
    tmp_path: Path,
) -> None:
    path = tmp_path / "graph.db"
    _ready_graph(path)
    with Database(path) as database:
        other_project_id = database.add_project(
            "other", str(tmp_path / "other"), TRUSTED_LOCAL_OWNER, "project:other"
        )
        other_file_id = database.add_file(
            other_project_id, str(tmp_path / "other" / "private" / "secret.py"), "c"
        )
        database.add_node(other_project_id, other_file_id, "class", "Secret", 1, 2)
        generation_id = int(
            database.conn.execute(
                "SELECT id FROM index_generations WHERE project_id=?", (other_project_id,)
            ).fetchone()[0]
        )
        database.conn.execute(
            "UPDATE index_generations SET query_ready=1, manifest_sha256=? WHERE id=?",
            ("b" * 64, generation_id),
        )

    service = StructuralGraphService(path)
    complete = service.graph().as_dict()
    other_project = next(
        node
        for node in complete["nodes"]
        if node["type"] == "project"
        and _detail_value(node, "project_id") == other_project_id
    )
    other_folder_id = str(
        next(
            node["id"]
            for node in complete["nodes"]
            if node["type"] == "folder"
            and other_project["id"] in _ancestor_ids(node)
        )
    )

    with pytest.raises(ValueError, match="selected_folder_id"):
        service.graph(project_id=1, selected_folder_id=other_folder_id)
