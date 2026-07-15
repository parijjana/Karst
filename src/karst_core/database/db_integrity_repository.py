from __future__ import annotations

from src.karst_core.database.db_graph_repository import IntegrityReport, OperationalRepositoryMixin


class IntegrityRepositoryMixin(OperationalRepositoryMixin):
    """Embedding compatibility plus generation-aware integrity reporting."""

    def upsert_embedding(
        self,
        node_id: int,
        vector: str,
        *,
        content_hash: str | None = None,
        model_revision: str | None = None,
    ) -> int:
        with self.transaction():
            node = self.conn.execute(
                "SELECT node.project_id, node.generation_id FROM nodes AS node "
                "JOIN index_generations AS generation ON generation.id = "
                "node.generation_id AND generation.project_id = node.project_id "
                "WHERE node.id = ? AND generation.status = 'active'",
                (node_id,),
            ).fetchone()
            if node is None:
                raise ValueError("Node does not belong to an active generation.")
            project_id, generation_id = int(node[0]), int(node[1])
            writable_generation_id = self._writable_active_generation_id(project_id)
            if generation_id != writable_generation_id:
                raise ValueError("Node does not belong to a writable active generation.")
            self.conn.execute(
                "INSERT INTO embeddings "
                "(project_id, generation_id, node_id, vector, content_hash, "
                "model_revision) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(node_id) DO UPDATE SET vector = excluded.vector, "
                "content_hash = excluded.content_hash, "
                "model_revision = excluded.model_revision",
                (
                    project_id,
                    generation_id,
                    node_id,
                    vector,
                    content_hash,
                    model_revision,
                ),
            )
            return int(
                self.conn.execute(
                    "SELECT id FROM embeddings WHERE node_id = ?", (node_id,)
                ).fetchone()[0]
            )

    def integrity_report(self) -> IntegrityReport:
        self._ensure_open()
        result = str(self.conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = tuple(
            tuple(row) for row in self.conn.execute("PRAGMA foreign_key_check")
        )
        consistency = self.conn.execute(
            "SELECT 'missing_active_generation', project.id FROM projects AS project "
            "LEFT JOIN index_generations AS generation ON generation.project_id = "
            "project.id AND generation.status = 'active' WHERE generation.id IS NULL "
            "UNION ALL SELECT 'node_file_scope', node.id FROM nodes AS node "
            "JOIN files AS file ON file.id = node.file_id WHERE file.project_id != "
            "node.project_id OR file.generation_id != node.generation_id "
            "UNION ALL SELECT 'edge_source_scope', edge.id FROM edges AS edge "
            "JOIN nodes AS node ON node.id = edge.source_id WHERE node.project_id != "
            "edge.project_id OR node.generation_id != edge.generation_id "
            "UNION ALL SELECT 'edge_target_scope', edge.id FROM edges AS edge "
            "JOIN nodes AS node ON node.id = edge.target_id WHERE node.project_id != "
            "edge.project_id OR node.generation_id != edge.generation_id "
            "UNION ALL SELECT 'embedding_node_scope', embedding.id FROM embeddings "
            "AS embedding JOIN nodes AS node ON node.id = embedding.node_id WHERE "
            "node.project_id != embedding.project_id OR node.generation_id != "
            "embedding.generation_id ORDER BY 1, 2"
        ).fetchall()
        return IntegrityReport(
            result,
            foreign_keys,
            tuple((str(row[0]), int(row[1])) for row in consistency),
        )
