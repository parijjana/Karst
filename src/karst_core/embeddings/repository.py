from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from src.karst_core.database.database import Database


@dataclass(frozen=True, slots=True)
class EmbeddingRecord:
    node_id: int
    vector: str
    content_hash: str | None = None
    model_revision: str | None = None


def get_node_text(database: Database, node_id: int) -> str:
    row = database.conn.execute(
        "SELECT n.type, n.name, n.start_line, n.end_line, f.path "
        "FROM nodes AS n JOIN files AS f ON n.file_id = f.id WHERE n.id = ?",
        (node_id,),
    ).fetchone()
    if row is None:
        return ""

    node_type, name, start_line, end_line, file_path = row
    try:
        with Path(file_path).open("r", encoding="utf-8") as source:
            lines = source.readlines()
        snippet = "".join(lines[start_line - 1 : end_line])
        return f"{node_type} {name}\n{snippet}"
    except OSError:
        return f"{node_type} {name}"


def pending_node_ids(database: Database) -> tuple[int, ...]:
    rows = database.conn.execute(
        "SELECT node.id FROM nodes AS node "
        "LEFT JOIN embeddings AS embedding ON embedding.node_id = node.id "
        "WHERE embedding.node_id IS NULL ORDER BY node.id"
    ).fetchall()
    return tuple(int(row[0]) for row in rows)


def store_embedding_batch(
    database: Database,
    records: Sequence[EmbeddingRecord],
) -> None:
    with database.transaction():
        for record in records:
            database.upsert_embedding(
                record.node_id,
                record.vector,
                content_hash=record.content_hash,
                model_revision=record.model_revision,
            )
