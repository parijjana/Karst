from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from src.karst_core.database.database import Database
from src.karst_core.database.db_migrations import MIGRATIONS, migrate
from src.index_identity import derive_file_stable_id
from tests.database_v2_generation_support import (
    PROJECT_STABLE_ID,
    create_v2_database,
)


def test_v1_v2_migration_identities_and_checksums_are_frozen() -> None:
    assert [
        (item.version, item.name, item.definition, item.checksum)
        for item in MIGRATIONS[:2]
    ] == [
        (
            1,
            "establish legacy baseline",
            "karst-legacy-tables-v1",
            "996c87ed5846591773e0dff07b50c7818046ddabd1fba7e654d2b1913a6b8073",
        ),
        (
            2,
            "harden identities and indexes",
            "karst-hardened-schema-v2-conflict-audit-composite-fks",
            "9fdcaf014b812f7ca9d1f1cf21fd596cfba913bc058eae2f66789b01eaacc3fb",
        ),
    ]


def test_preacceptance_v3_checksum_tracks_lossless_legacy_text_contract() -> None:
    # V3 is not released yet, so this checksum intentionally moved while V1/V2 stayed frozen.
    migration = MIGRATIONS[2]

    assert migration.definition == (
        "karst-generation-schema-v3-lossless-legacy-text-query-readiness-identity-path"
    )
    assert migration.checksum == (
        "b82b10781b272957da83c273baad092e3bd23d3c666120185a10783cb8f67040"
    )


def test_v2_populated_and_empty_projects_bootstrap_one_active_generation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bootstrap.db"
    create_v2_database(path)

    with Database(path) as database:
        generations = database.conn.execute(
            "SELECT project_id, ordinal, status, query_ready, manifest_sha256, "
            "discovered_files, indexed_files, unchanged_files, skipped_files, "
            "deleted_files, renamed_files, failed_files, symbol_count, edge_count, "
            "diagnostic_count FROM index_generations ORDER BY project_id"
        ).fetchall()

        assert database.schema_version == 3
        assert len(generations) == 2
        assert tuple(generations[0][:5]) == (7, 1, "active", 0, None)
        assert tuple(generations[0][5:]) == (1, 1, 0, 0, 0, 0, 0, 2, 1, 0)
        assert tuple(generations[1][:5]) == (8, 1, "active", 0, None)
        assert tuple(generations[1][5:]) == (0,) * 10


def test_empty_v2_database_upgrades_without_fabricating_generations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "empty-v2.db"
    connection = sqlite3.connect(path)
    migrate(connection, migrations=MIGRATIONS[:2])
    connection.close()

    with Database(path) as database:
        assert database.schema_version == 3
        assert (
            database.conn.execute("SELECT COUNT(*) FROM index_generations").fetchone()[
                0
            ]
            == 0
        )
        assert database.integrity_report().ok


def test_v2_bootstrap_preserves_ids_graph_embeddings_and_operational_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "preservation.db"
    create_v2_database(path)
    before = sqlite3.connect(path)
    audit_before = tuple(before.execute("SELECT * FROM migration_audit").fetchone())
    conflicts_before = before.execute("SELECT * FROM migration_conflicts").fetchall()
    projects_before = [
        tuple(row) for row in before.execute("SELECT * FROM projects ORDER BY id")
    ]
    before.close()

    with Database(path) as database:
        generation_id = int(
            database.conn.execute(
                "SELECT id FROM index_generations WHERE project_id = 7"
            ).fetchone()[0]
        )
        file_row = database.conn.execute(
            "SELECT id, project_id, generation_id, stable_id, path, relative_path, "
            "identity_path, hash, byte_size FROM files"
        ).fetchone()
        nodes = database.conn.execute(
            "SELECT id, project_id, generation_id, file_id, stable_id, language, "
            "type, name, qualified_name, signature, overload_discriminator, "
            "start_line, end_line FROM nodes ORDER BY id"
        ).fetchall()

        assert tuple(file_row[:3]) == (11, 7, generation_id)
        assert tuple(file_row[4:]) == (
            "/legacy/project/src/a.py",
            "src/a.py",
            "src/a.py",
            "legacy-hash",
            0,
        )
        assert file_row[3] == derive_file_stable_id(PROJECT_STABLE_ID, "src/a.py")
        assert [row[0] for row in nodes] == [21, 22]
        assert all(tuple(row[1:4]) == (7, generation_id, 11) for row in nodes)
        assert all(
            row[5:10] == ("python", "function", "run", "run", None) for row in nodes
        )
        assert [row[10] for row in nodes] == ["legacy:21", "legacy:22"]
        assert nodes[0][4] != nodes[1][4]
        assert all(UUID(str(row[4])).version == 5 for row in nodes)
        assert [tuple(row) for row in database.conn.execute("SELECT * FROM edges")] == [
            (31, 7, generation_id, 21, 22, "calls")
        ]
        assert [
            tuple(row)
            for row in database.conn.execute("SELECT * FROM embeddings ORDER BY id")
        ] == [
            (41, 7, generation_id, 21, "[0.1]", "content-one", "model@1"),
            (42, 7, generation_id, 22, "[0.2]", "content-two", "model@1"),
        ]
        assert tuple(database.conn.execute("SELECT * FROM commits").fetchone()) == (
            51,
            7,
            "abc",
            "legacy commit",
            "2025-01-02 03:04:05",
        )
        assert tuple(
            database.conn.execute("SELECT * FROM commit_files").fetchone()
        ) == (52, 51, "src/a.py", "M")
        assert tuple(database.conn.execute("SELECT * FROM telemetry").fetchone()) == (
            61,
            7,
            "legacy_tool",
            1.5,
            9,
            "kept",
            "2025-01-02 03:04:06",
        )
        assert tuple(
            database.conn.execute("SELECT * FROM active_processes").fetchone()
        ) == (
            71,
            "legacy.py",
            "2025-01-02 03:04:07",
            "running",
        )
        assert (
            tuple(database.conn.execute("SELECT * FROM migration_audit").fetchone())
            == audit_before
        )
        assert [
            tuple(row)
            for row in database.conn.execute("SELECT * FROM migration_conflicts")
        ] == conflicts_before
        assert [
            tuple(row)
            for row in database.conn.execute("SELECT * FROM projects ORDER BY id")
        ] == projects_before


def test_quarantined_project_identity_is_deterministic(tmp_path: Path) -> None:
    identities: list[tuple[str, tuple[str, ...]]] = []
    for name in ("first.db", "second.db"):
        path = tmp_path / name
        create_v2_database(path, project_stable_id=None)
        with Database(path) as database:
            file_id = str(
                database.conn.execute("SELECT stable_id FROM files").fetchone()[0]
            )
            symbols = tuple(
                str(row[0])
                for row in database.conn.execute(
                    "SELECT stable_id FROM nodes ORDER BY id"
                )
            )
            identities.append((file_id, symbols))

    assert identities[0] == identities[1]
