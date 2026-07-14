from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from typing import Any


MAX_SEMANTIC_RESULTS = 100
SEMANTIC_MODEL_NAME = "BAAI/bge-small-en-v1.5"
SEMANTIC_MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
_embed_model: Any | None = None


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


def get_embed_model() -> Any:
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer

        _embed_model = SentenceTransformer(
            SEMANTIC_MODEL_NAME,
            revision=SEMANTIC_MODEL_REVISION,
            local_files_only=True,
            trust_remote_code=False,
        )
    return _embed_model


def cosine_similarity(vector_one: list[float], vector_two: list[float]) -> float:
    if not vector_one or len(vector_one) != len(vector_two):
        return 0.0
    norm_one = math.sqrt(sum(value * value for value in vector_one))
    norm_two = math.sqrt(sum(value * value for value in vector_two))
    if norm_one == 0.0 or norm_two == 0.0:
        return 0.0
    dot_product = sum(left * right for left, right in zip(vector_one, vector_two))
    return dot_product / (norm_one * norm_two)


def do_semantic_search(
    db: Any, project_id: int, query: str, limit: int = 5
) -> tuple[str, float, int]:
    start_time = time.monotonic()
    if not 1 <= limit <= MAX_SEMANTIC_RESULTS:
        return (
            f"Semantic search limit must be between 1 and {MAX_SEMANTIC_RESULTS}.",
            time.monotonic() - start_time,
            0,
        )

    cursor = db.conn.cursor()
    try:
        cursor.execute(
            """
            SELECT n.id, n.file_id, n.type, n.name,
                   n.start_line, n.end_line, e.vector
            FROM embeddings e
            JOIN nodes n ON e.node_id = n.id
            WHERE n.project_id = ?
            """,
            (project_id,),
        )
    except sqlite3.OperationalError:
        return (
            "Semantic search is not ready for this database.",
            time.monotonic() - start_time,
            0,
        )

    rows = cursor.fetchall()
    if not rows:
        return (
            f"No semantic matches found for '{query}'.",
            time.monotonic() - start_time,
            0,
        )

    model = get_embed_model()
    query_vector = [float(value) for value in model.encode(query).tolist()]

    results: list[tuple[float, int, str, str, int, int]] = []
    for row in rows:
        _, file_id, node_type, name, start_line, end_line, encoded = row
        try:
            vector = [float(value) for value in json.loads(encoded)]
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        similarity = cosine_similarity(query_vector, vector)
        if len(vector) != len(query_vector):
            continue
        results.append((similarity, file_id, node_type, name, start_line, end_line))

    results.sort(key=lambda item: (-item[0], item[3], item[1]))
    top_results = results[:limit]
    if not top_results:
        return (
            f"No semantic matches found for '{query}'.",
            time.monotonic() - start_time,
            0,
        )

    output = [f"Top {len(top_results)} semantic matches for '{query}':"]
    for similarity, file_id, node_type, name, start_line, end_line in top_results:
        cursor.execute("SELECT path FROM files WHERE id = ?", (file_id,))
        file_row = cursor.fetchone()
        file_path = file_row[0] if file_row else "Unknown file"
        output.append(
            f"- [{similarity:.3f}] {node_type} '{name}' at "
            f"{file_path}:{start_line}-{end_line}"
        )

    response = "\n".join(output)
    return response, time.monotonic() - start_time, max(1, len(response) // 4)
