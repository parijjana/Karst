from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Callable
from typing import Any

from .model import get_embed_model


MAX_SEMANTIC_RESULTS = 100


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
    db: Any,
    project_id: int,
    query: str,
    limit: int = 5,
    *,
    model_provider: Callable[[], Any] | None = None,
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

    model = (model_provider or get_embed_model)()
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
