from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from src.web_auth import request_settings
from src.web_data import get_db


router = APIRouter()


@router.get("/api/graph")
async def get_graph(
    request: Request, project_id: int | None = None
) -> dict[str, list[dict[str, object]]]:
    configured = request_settings(request)
    if not configured.db_path.exists():
        return {"nodes": [], "links": []}
    graph_limit = min(configured.dashboard_max_page_size, 400)

    with get_db(configured.db_path) as connection:
        if project_id is None:
            project_rows = connection.execute(
                "SELECT id, name, path FROM projects ORDER BY id LIMIT ?",
                (graph_limit,),
            ).fetchall()
        else:
            project_rows = connection.execute(
                "SELECT id, name, path FROM projects WHERE id = ? LIMIT 1",
                (project_id,),
            ).fetchall()
        projects = {row["id"]: dict(row) for row in project_rows}
        if not projects:
            return {"nodes": [], "links": []}

        nodes: list[dict[str, object]] = [
            {
                "id": f"project_{project_key}",
                "name": project["name"],
                "type": "project",
                "group": project_key,
            }
            for project_key, project in projects.items()
        ]
        links: list[dict[str, object]] = []
        project_ids = tuple(projects)
        placeholders = ",".join("?" for _ in project_ids)
        file_rows = connection.execute(
            f"""
            SELECT id, path, project_id FROM files
            WHERE project_id IN ({placeholders}) ORDER BY id LIMIT ?
            """,
            (*project_ids, graph_limit),
        ).fetchall()
        file_ids: list[int] = []
        for row in file_rows:
            file_ids.append(row["id"])
            file_id = f"file_{row['id']}"
            nodes.append(
                {
                    "id": file_id,
                    "name": Path(row["path"]).name,
                    "type": "file",
                    "group": row["project_id"],
                }
            )
            links.append({"source": file_id, "target": f"project_{row['project_id']}"})

        selected_nodes = _append_symbol_nodes(
            connection, file_ids, graph_limit, nodes, links
        )
        if selected_nodes:
            placeholders = ",".join("?" for _ in selected_nodes)
            edges = connection.execute(
                f"""
                SELECT source_id, target_id FROM edges
                WHERE source_id IN ({placeholders})
                  AND target_id IN ({placeholders})
                ORDER BY id LIMIT ?
                """,
                (*selected_nodes, *selected_nodes, graph_limit),
            ).fetchall()
            links.extend(
                {
                    "source": f"node_{row['source_id']}",
                    "target": f"node_{row['target_id']}",
                }
                for row in edges
            )
        return {"nodes": nodes, "links": links}


def _append_symbol_nodes(
    connection,
    file_ids: list[int],
    graph_limit: int,
    nodes: list[dict[str, object]],
    links: list[dict[str, object]],
) -> list[int]:
    if not file_ids:
        return []
    placeholders = ",".join("?" for _ in file_ids)
    rows = connection.execute(
        f"""
        SELECT id, file_id, type, name, project_id FROM nodes
        WHERE file_id IN ({placeholders}) ORDER BY id LIMIT ?
        """,
        (*file_ids, graph_limit),
    ).fetchall()
    selected: list[int] = []
    for row in rows:
        selected.append(row["id"])
        node_id = f"node_{row['id']}"
        nodes.append(
            {
                "id": node_id,
                "name": row["name"],
                "type": row["type"],
                "group": row["project_id"],
            }
        )
        links.append({"source": node_id, "target": f"file_{row['file_id']}"})
    return selected
