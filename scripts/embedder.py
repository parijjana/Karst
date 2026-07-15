from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from src.database import Database
from src.core_settings import core_settings


MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
EMBEDDING_BATCH_SIZE = 64


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


def get_embed_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        MODEL_NAME,
        revision=MODEL_REVISION,
        local_files_only=True,
        trust_remote_code=False,
    )


def main() -> None:
    core_settings.data_dir.mkdir(parents=True, exist_ok=True)
    with Database(core_settings.db_path) as database:
        nodes_to_embed = pending_node_ids(database)
        if not nodes_to_embed:
            return

        print(
            f"Loading {MODEL_NAME} model... "
            f"(Found {len(nodes_to_embed)} nodes to embed)"
        )
        model = get_embed_model()
        print("Model loaded. Starting embedding process...")
        started_at = time.monotonic()
        batch: list[EmbeddingRecord] = []
        embedded_count = 0
        for node_id in nodes_to_embed:
            text = get_node_text(database, node_id)
            if not text:
                continue
            vector = json.dumps(model.encode(text).tolist())
            batch.append(
                EmbeddingRecord(
                    node_id=node_id,
                    vector=vector,
                    content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    model_revision=MODEL_REVISION,
                )
            )
            if len(batch) >= EMBEDDING_BATCH_SIZE:
                store_embedding_batch(database, batch)
                embedded_count += len(batch)
                batch.clear()
        if batch:
            store_embedding_batch(database, batch)
            embedded_count += len(batch)

        database.log_telemetry(
            None,
            "service:embedder",
            (time.monotonic() - started_at) * 1000,
            embedded_count,
            json.dumps(
                {"model": MODEL_NAME, "revision": MODEL_REVISION}, sort_keys=True
            ),
        )
    print("Embedding complete.")


if __name__ == "__main__":
    main()
