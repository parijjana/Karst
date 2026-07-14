"""Atomic, bounded generation-based indexing orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.database import Database
from src.index_discovery import DiscoveryLimits, discover_snapshots
from src.index_models import (
    DiagnosticSeverity, IndexCounts, IndexDiagnostic, IndexResult, IndexStatus,
    ParseStatus,
)
from src.index_plan import ManifestRecord, PlanAction, build_manifest_plan
from src.index_repository import GenerationRepository
from src.parser import CodeParser
from src.security import PathSecurityPolicy, SecurityViolation


@dataclass(frozen=True, slots=True)
class IncrementalIndexService:
    database_factory: Callable[[], Database]
    policy: PathSecurityPolicy
    parser_factory: Callable[[], CodeParser] = CodeParser

    def index(self, project: int | str, root: str | Path, *,
              limits: DiscoveryLimits | None = None,
              cancel: Callable[[], bool] | None = None) -> IndexResult:
        try:
            if cancel and cancel():
                raise SecurityViolation("index_cancelled")
            discovery = discover_snapshots(root, self.policy, limits=limits, cancelled=cancel)
        except (SecurityViolation, ValueError, OSError) as exc:
            code = str(exc).split(":", 1)[0] or "index_rejected"
            diagnostic = IndexDiagnostic(DiagnosticSeverity.ERROR, _code(code), _code(code))
            return IndexResult(IndexStatus.REJECTED, IndexCounts(diagnostic_count=1), (diagnostic,))

        current = tuple(ManifestRecord.from_snapshot(s) for s in discovery.snapshots)
        try:
            with self.database_factory() as db:
                repo = GenerationRepository(db)
                active = repo.active(project)
                previous = self._manifest(db, active.id) if active else ()
                plan = build_manifest_plan(current, previous)
                generation = repo.clone(project) if active else repo.admit(project)
                parser = self.parser_factory()
                by_path = {s.candidate.relative_path: s for s in discovery.snapshots}
                diagnostics: list[IndexDiagnostic] = [
                    IndexDiagnostic(DiagnosticSeverity.WARNING, _code(code), _code(code))
                    for code in plan.diagnostics
                ]
                skipped = failed = indexed = 0
                for item in plan.items:
                    if cancel and cancel():
                        raise SecurityViolation("index_cancelled")
                    if item.action is PlanAction.UNCHANGED:
                        continue
                    if item.previous is not None and item.action is not PlanAction.RENAME:
                        self._remove_file(db, generation.id, item.previous.candidate.relative_path)
                    if item.action is PlanAction.RENAME and item.current is not None:
                        self._rename_file(db, generation.id, item.current)
                        continue
                    if item.current is None:
                        continue
                    parsed = parser.parse_snapshot(by_path[item.current.candidate.relative_path])
                    if parsed.status is ParseStatus.INDEXED:
                        repo.stage(generation.id, parsed)
                        indexed += 1
                    else:
                        for diagnostic in parsed.diagnostics:
                            repo.record_diagnostic(generation.id, diagnostic)
                            diagnostics.append(diagnostic)
                        if parsed.status is ParseStatus.FAILED:
                            failed += 1
                            raise ValueError("parse_failed")
                        skipped += 1
                diagnostics.extend(
                    IndexDiagnostic(DiagnosticSeverity.WARNING, _code(code), _code(code))
                    for code in plan.diagnostics if code not in {d.code for d in diagnostics}
                )
                counts = IndexCounts(
                    discovered_files=len(current), indexed_files=indexed,
                    unchanged_files=plan.counts.unchanged, skipped_files=skipped,
                    deleted_files=plan.counts.deleted, renamed_files=plan.counts.renamed,
                    failed_files=failed, diagnostic_count=len(diagnostics),
                )
                if counts.processed_files != counts.discovered_files:
                    raise ValueError("incomplete_index")
                promoted = repo.promote(generation.id)
                manifest = db.conn.execute(
                    "SELECT manifest_sha256 FROM index_generations WHERE id=?", (promoted.id,)
                ).fetchone()[0]
                return IndexResult(IndexStatus.COMPLETED, counts, tuple(diagnostics), promoted.id, str(manifest), True)
        except Exception as exc:
            code = _code("index_cancelled" if cancel and cancel() else str(exc).split(":", 1)[0])
            if 'generation' in locals():
                diag = IndexDiagnostic(DiagnosticSeverity.ERROR, code, code)
                try:
                    with self.database_factory() as db:
                        repo = GenerationRepository(db)
                        (repo.cancel if code == "index_cancelled" else repo.fail)(generation.id, code)
                except Exception:
                    pass
                status = IndexStatus.CANCELLED if code == "index_cancelled" else IndexStatus.FAILED
                return IndexResult(status, IndexCounts(failed_files=1, diagnostic_count=1), (diag,), generation.id)
            return IndexResult(IndexStatus.FAILED, IndexCounts(failed_files=1, diagnostic_count=1), (IndexDiagnostic(DiagnosticSeverity.ERROR, code, code),))

    @staticmethod
    def _manifest(db: Database, generation_id: int) -> tuple[ManifestRecord, ...]:
        from src.index_identity import FileCandidate
        rows = db.conn.execute("SELECT p.stable_id,f.relative_path,f.stable_id,f.hash,f.byte_size,f.identity_path FROM files f JOIN projects p ON p.id=f.project_id WHERE f.generation_id=? ORDER BY f.relative_path", (generation_id,)).fetchall()
        return tuple(ManifestRecord(FileCandidate(str(r[0]), str(r[1]), str(r[2]), identity_path=str(r[5])), str(r[3]), int(r[4])) for r in rows)

    @staticmethod
    def _remove_file(db: Database, generation_id: int, relative_path: str) -> None:
        with db.transaction():
            db.conn.execute("DELETE FROM files WHERE generation_id=? AND relative_path=?", (generation_id, relative_path))

    @staticmethod
    def _rename_file(db: Database, generation_id: int, record: ManifestRecord) -> None:
        with db.transaction():
            db.conn.execute(
                """UPDATE files SET relative_path=?,
                   path=(SELECT path FROM projects WHERE id=files.project_id)||'/'||?
                   WHERE generation_id=? AND stable_id=?""",
                (record.candidate.relative_path, record.candidate.relative_path,
                 generation_id, record.candidate.stable_file_id),
            )


def _code(value: str) -> str:
    value = value.strip().lower().replace("-", "_")
    return value if value and value[0].isalpha() else "index_rejected"


GenerationIndexService = IncrementalIndexService
