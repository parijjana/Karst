from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from src.karst_core.database.database import Database
from src.parser import CodeParser, ParseStatus, ParseSummary
from src.security import (
    PathSecurityPolicy,
    SecurityViolation,
    stable_project_id,
    validate_registered_project,
)
from src.core_settings import CoreSettings, TRUSTED_LOCAL_OWNER, core_settings


logger = logging.getLogger(__name__)
VALID_EXTENSIONS = {".py", ".js", ".ts", ".dart", ".md"}
IGNORED_DIRECTORIES = {
    "node_modules",
    "build",
    "dist",
    "__pycache__",
    "out",
    "target",
}


@dataclass(frozen=True, slots=True)
class ReindexReport:
    project_name: str
    indexed_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    quarantined: bool = False
    security_code: str | None = None


def reindex_projects(configuration: CoreSettings = core_settings) -> tuple[ReindexReport, ...]:
    """Reindex trusted projects from the canonical Karst database."""
    configuration.data_dir.mkdir(parents=True, exist_ok=True)
    database = Database(str(configuration.db_path))
    reports: list[ReindexReport] = []
    try:
        projects = database.conn.execute(
            "SELECT name, path, owner, stable_id FROM projects ORDER BY name"
        ).fetchall()
        policy = PathSecurityPolicy(configuration.allowed_roots)
        parser = CodeParser()

        for project_name, stored_path, stored_owner, stored_id in projects:
            start_time = time.monotonic()
            try:
                canonical_root = validate_registered_project(
                    policy, stored_path, stored_owner, stored_id
                )
                files_to_index = policy.discover_project_files(
                    canonical_root, VALID_EXTENSIONS, IGNORED_DIRECTORIES
                )
            except SecurityViolation as error:
                logger.error(
                    "Quarantined project %s because its persisted identity is unsafe: %s",
                    project_name,
                    error.code,
                )
                reports.append(
                    ReindexReport(
                        project_name=project_name,
                        quarantined=True,
                        security_code=error.code,
                    )
                )
                continue

            existing = database.conn.execute(
                "SELECT id FROM projects WHERE name = ?", (project_name,)
            ).fetchone()
            if existing is None:
                reports.append(
                    ReindexReport(
                        project_name=project_name,
                        quarantined=True,
                        security_code="project_identity_invalid",
                    )
                )
                continue

            database.clear_project_data(existing[0])
            project_id = database.add_project(
                project_name,
                str(canonical_root),
                TRUSTED_LOCAL_OWNER,
                stable_project_id(canonical_root),
            )
            outcomes = []
            bytes_processed = 0
            for file_path in files_to_index:
                outcome = parser.parse_file(database, project_id, file_path)
                outcomes.append(outcome)
                if outcome.status is ParseStatus.INDEXED:
                    try:
                        bytes_processed += file_path.stat().st_size
                    except OSError:
                        pass

            summary = ParseSummary(tuple(outcomes))
            database.log_telemetry(
                project_id,
                "service:reindexer",
                (time.monotonic() - start_time) * 1000,
                int(bytes_processed / 4),
                json.dumps(
                    {
                        "bytes_processed": bytes_processed,
                        "indexed": summary.indexed_count,
                        "skipped": summary.skipped_count,
                        "failed": summary.failed_count,
                    },
                    sort_keys=True,
                ),
            )
            reports.append(
                ReindexReport(
                    project_name=project_name,
                    indexed_count=summary.indexed_count,
                    skipped_count=summary.skipped_count,
                    failed_count=summary.failed_count,
                )
            )
    finally:
        database.close()
    return tuple(reports)


def main() -> None:
    reindex_projects()


if __name__ == "__main__":
    main()
