from __future__ import annotations

import hashlib
import json
import time

from src.karst_core.embeddings import (
    MODEL_NAME,
    MODEL_REVISION,
    EmbeddingRecord,
    create_embed_model,
    get_node_text,
    pending_node_ids,
    store_embedding_batch,
)
from src.karst_core.database.database import Database
from src.core_settings import core_settings


EMBEDDING_BATCH_SIZE = 64


def get_embed_model():
    return create_embed_model()


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
