from __future__ import annotations

import os
import sqlite3
import time
from typing import Any


def do_find_deps(
    db: Any,
    project_id: int,
    symbol_name: str,
    is_dependent: bool,
) -> tuple[str, float, int]:
    start_time = time.monotonic()
    node = db.get_node_by_name(project_id, symbol_name)
    if not node:
        return f"Symbol '{symbol_name}' not found.", time.monotonic() - start_time, 0

    cursor = db.conn.cursor()
    if is_dependent:
        cursor.execute(
            """
            SELECT n.name, n.type, e.type
            FROM edges e
            JOIN nodes n ON e.source_id = n.id
            WHERE e.target_id = ?
            ORDER BY n.name, n.id, e.id
            """,
            (node["id"],),
        )
        label = "Dependents"
    else:
        cursor.execute(
            """
            SELECT n.name, n.type, e.type
            FROM edges e
            JOIN nodes n ON e.target_id = n.id
            WHERE e.source_id = ?
            ORDER BY n.name, n.id, e.id
            """,
            (node["id"],),
        )
        label = "Dependencies"
    dependencies = cursor.fetchall()

    if not dependencies:
        return (
            f"No {label.lower()} found for '{symbol_name}'.",
            time.monotonic() - start_time,
            0,
        )

    result = [f"{label} for '{symbol_name}':"]
    result.extend(
        f"- {name} ({node_type}) [edge: {edge_type}]"
        for name, node_type, edge_type in dependencies
    )
    response = "\n".join(result)
    tokens_saved = _estimated_tokens_saved(cursor, node["file_id"], response)
    return response, time.monotonic() - start_time, tokens_saved


def _estimated_tokens_saved(cursor: Any, file_id: int, response: str) -> int:
    try:
        cursor.execute("SELECT path FROM files WHERE id = ?", (file_id,))
        file_row = cursor.fetchone()
        file_path = file_row[0] if file_row else None
        raw_size = os.path.getsize(file_path) if file_path else 0
        return max(0, int((raw_size - len(response)) / 4))
    except (OSError, TypeError, sqlite3.DatabaseError):
        return 0


__all__ = ["do_find_deps"]
