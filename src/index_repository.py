"""Atomic staging and promotion repository for immutable index generations."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from dataclasses import dataclass
from typing import Iterable

from src.karst_core.database.database import Database
from src.index_identity import SourceSnapshot
from src.index_models import DiagnosticSeverity, IndexDiagnostic, ParseStatus, ParsedFile


@dataclass(frozen=True, slots=True)
class Generation:
    id: int
    project_id: int
    ordinal: int
    status: str
    query_ready: bool


class IndexRepository:
    """Short-transaction writer; active generations are never modified."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def _project(self, project: int | str) -> int:
        if isinstance(project, bool):
            raise TypeError("project must be an integer id or stable id")
        if isinstance(project, int):
            row = self.database.conn.execute("SELECT id FROM projects WHERE id=?", (project,)).fetchone()
        else:
            row = self.database.conn.execute("SELECT id FROM projects WHERE stable_id=?", (project,)).fetchone()
        if row is None:
            raise ValueError("project does not exist")
        return int(row[0])

    def admit(self, project: int | str) -> Generation:
        pid = self._project(project)
        with self.database.transaction():
            if self.database.conn.execute("SELECT 1 FROM index_generations WHERE project_id=? AND status='staging'", (pid,)).fetchone():
                raise ValueError("project already has a staging generation")
            ordinal = int(self.database.conn.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM index_generations WHERE project_id=?", (pid,)).fetchone()[0])
            cur = self.database.conn.execute("INSERT INTO index_generations(project_id,ordinal,status) VALUES(?,?,'staging')", (pid, ordinal))
            gid = int(cur.lastrowid or 0)
        return Generation(gid, pid, ordinal, "staging", False)

    start = admit

    def stage(self, generation_id: int, parsed: ParsedFile) -> None:
        if not isinstance(parsed, ParsedFile):
            raise TypeError("parsed must be ParsedFile")
        with self.database.transaction():
            row = self.database.conn.execute("SELECT project_id,status FROM index_generations WHERE id=?", (generation_id,)).fetchone()
            if row is None or row[1] != "staging":
                raise ValueError("generation is not staging")
            if parsed.status is ParseStatus.FAILED:
                raise ValueError("failed parses cannot be staged as files")
            pid = int(row[0])
            snap = parsed.snapshot
            c = snap.candidate
            project = self.database.conn.execute("SELECT stable_id,path FROM projects WHERE id=?", (pid,)).fetchone()
            if project is None or project[0] != snap.candidate.project_stable_id:
                raise ValueError("file belongs to another project")
            root = Path(str(project[1])).resolve()
            absolute = (root / snap.candidate.relative_path).resolve()
            if root not in absolute.parents and absolute != root:
                raise ValueError("path escapes project root")
            try:
                cur = self.database.conn.execute(
                "INSERT INTO files(project_id,generation_id,stable_id,path,relative_path,identity_path,hash,byte_size,nonblank_lines) VALUES(?,?,?,?,?,?,?,?,?)",
                    (pid,generation_id,c.stable_file_id,str(absolute),c.relative_path,c.identity_path,snap.content_sha256,snap.byte_size,_nonblank_lines(snap.content)),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("file is already staged in this generation") from exc
            fid = int(cur.lastrowid or 0)
            for symbol in parsed.symbols:
                try:
                    self.database.conn.execute(
                        "INSERT INTO nodes(project_id,generation_id,file_id,stable_id,language,type,name,qualified_name,signature,overload_discriminator,start_line,end_line) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (pid,generation_id,fid,symbol.stable_symbol_id,symbol.language,symbol.kind,symbol.name,symbol.qualified_name,symbol.signature,symbol.overload_discriminator,symbol.start_line,symbol.end_line),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError("symbol is already staged in this generation") from exc
            for d in parsed.diagnostics:
                self._diagnostic(pid, generation_id, d)

    stage_file = stage

    def stage_untracked_paths(
        self, generation_id: int, paths: Iterable[tuple[str, str]]
    ) -> None:
        """Persist the bounded non-tracked inventory for one staging generation."""
        with self.database.transaction():
            row = self.database.conn.execute(
                "SELECT project_id, status FROM index_generations WHERE id=?", (generation_id,)
            ).fetchone()
            if row is None or row[1] != "staging":
                raise ValueError("generation is not staging")
            values = [(int(row[0]), generation_id, path, kind) for path, kind in paths]
            if any(kind not in {"file", "folder"} or not path for _project, _generation, path, kind in values):
                raise ValueError("invalid untracked path")
            self.database.conn.execute(
                "DELETE FROM untracked_paths WHERE project_id=? AND generation_id=?",
                (int(row[0]), generation_id),
            )
            self.database.conn.executemany(
                "INSERT INTO untracked_paths(project_id,generation_id,relative_path,kind) VALUES(?,?,?,?)",
                values,
            )

    def refresh_nonblank_lines(
        self, generation_id: int, snapshots: Iterable[SourceSnapshot]
    ) -> None:
        """Refresh staging LOC from the exact bytes represented by its manifest."""
        values = tuple(snapshots)
        by_path = {snapshot.candidate.relative_path: snapshot for snapshot in values}
        if len(by_path) != len(values):
            raise ValueError("discovered snapshots contain duplicate paths")
        with self.database.transaction():
            row = self.database.conn.execute(
                "SELECT status FROM index_generations WHERE id=?", (generation_id,)
            ).fetchone()
            if row is None or row[0] != "staging":
                raise ValueError("generation is not staging")
            files = self.database.conn.execute(
                "SELECT relative_path,hash,byte_size FROM files WHERE generation_id=?",
                (generation_id,),
            ).fetchall()
            for relative_path, digest, byte_size in files:
                snapshot = by_path.get(str(relative_path))
                if snapshot is None or (
                    snapshot.content_sha256,
                    snapshot.byte_size,
                ) != (str(digest), int(byte_size)):
                    raise ValueError("staging manifest does not match discovered snapshot")
                updated = self.database.conn.execute(
                    """UPDATE files SET nonblank_lines=?
                       WHERE generation_id=? AND relative_path=?
                       AND hash=? AND byte_size=?""",
                    (
                        _nonblank_lines(snapshot.content),
                        generation_id,
                        snapshot.candidate.relative_path,
                        snapshot.content_sha256,
                        snapshot.byte_size,
                    ),
                )
                if updated.rowcount != 1:
                    raise ValueError("staging manifest does not match discovered snapshot")

    def stage_edges(self, generation_id: int, edges: Iterable[tuple[int, int, str]]) -> None:
        """Stage validated node-id edges; each edge is inserted atomically."""
        values = tuple(edges)
        with self.database.transaction():
            row = self.database.conn.execute("SELECT project_id,status FROM index_generations WHERE id=?", (generation_id,)).fetchone()
            if row is None or row[1] != "staging":
                raise ValueError("generation is not staging")
            pid = int(row[0])
            for source, target, kind in values:
                if isinstance(source, bool) or isinstance(target, bool) or not isinstance(kind, str) or not kind:
                    raise ValueError("invalid edge")
                if isinstance(source, str):
                    found = self.database.conn.execute("SELECT id FROM nodes WHERE project_id=? AND generation_id=? AND stable_id=?", (pid,generation_id,source)).fetchone()
                    if found is None:
                        raise ValueError("unresolved source symbol")
                    source = int(found[0])
                elif isinstance(source, int):
                    found = self.database.conn.execute(
                        "SELECT 1 FROM nodes WHERE id=? AND project_id=? AND generation_id=?",
                        (source, pid, generation_id),
                    ).fetchone()
                    if found is None:
                        raise ValueError("source symbol belongs to another generation")
                else:
                    raise ValueError("invalid source symbol")
                if isinstance(target, str):
                    found = self.database.conn.execute("SELECT id FROM nodes WHERE project_id=? AND generation_id=? AND stable_id=?", (pid,generation_id,target)).fetchone()
                    if found is None:
                        raise ValueError("unresolved target symbol")
                    target = int(found[0])
                elif isinstance(target, int):
                    found = self.database.conn.execute(
                        "SELECT 1 FROM nodes WHERE id=? AND project_id=? AND generation_id=?",
                        (target, pid, generation_id),
                    ).fetchone()
                    if found is None:
                        raise ValueError("target symbol belongs to another generation")
                else:
                    raise ValueError("invalid target symbol")
                try:
                    self.database.conn.execute("INSERT INTO edges(project_id,generation_id,source_id,target_id,type) VALUES(?,?,?,?,?)", (pid,generation_id,source,target,kind))
                except sqlite3.IntegrityError as exc:
                    raise ValueError("invalid or duplicate edge") from exc

    def _diagnostic(self, pid: int, gid: int, d: IndexDiagnostic) -> None:
        self.database.conn.execute("INSERT INTO index_diagnostics(project_id,generation_id,relative_path,severity,code,message,exception_type) VALUES(?,?,?,?,?,?,?)", (pid,gid,d.relative_path,d.severity.value,d.code,d.message,d.exception_type))

    def record_diagnostic(self, generation_id: int, diagnostic: IndexDiagnostic) -> None:
        with self.database.transaction():
            row = self.database.conn.execute("SELECT project_id,status FROM index_generations WHERE id=?", (generation_id,)).fetchone()
            if row is None or row[1] != "staging":
                raise ValueError("generation is not staging")
            self._diagnostic(int(row[0]), generation_id, diagnostic)

    def promote(self, generation_id: int, *, query_ready: bool = True) -> Generation:
        if not query_ready:
            raise ValueError("promoted generations must be query-ready")
        with self.database.transaction():
            row = self.database.conn.execute("SELECT project_id,ordinal,status FROM index_generations WHERE id=?", (generation_id,)).fetchone()
            if row is None or row[2] != "staging":
                raise ValueError("generation is not staging")
            pid, ordinal = int(row[0]), int(row[1])
            counts = self.database.conn.execute("SELECT COUNT(*) FROM files WHERE project_id=? AND generation_id=?", (pid,generation_id)).fetchone()[0]
            symbols = self.database.conn.execute("SELECT COUNT(*) FROM nodes WHERE project_id=? AND generation_id=?", (pid,generation_id)).fetchone()[0]
            edges = self.database.conn.execute("SELECT sn.stable_id,tn.stable_id,e.type FROM edges e JOIN nodes sn ON sn.id=e.source_id AND sn.generation_id=e.generation_id JOIN nodes tn ON tn.id=e.target_id AND tn.generation_id=e.generation_id WHERE e.project_id=? AND e.generation_id=? ORDER BY sn.stable_id,tn.stable_id,e.type", (pid,generation_id)).fetchall()
            diagnostics = self.database.conn.execute("SELECT COUNT(*) FROM index_diagnostics WHERE project_id=? AND generation_id=?", (pid,generation_id)).fetchone()[0]
            fatal = self.database.conn.execute("SELECT 1 FROM index_diagnostics WHERE project_id=? AND generation_id=? AND severity IN ('error','fatal') LIMIT 1", (pid,generation_id)).fetchone()
            if fatal:
                raise ValueError("generation contains terminal diagnostics")
            payload = self.database.conn.execute("SELECT stable_id,hash,byte_size FROM files WHERE project_id=? AND generation_id=? ORDER BY relative_path", (pid, generation_id)).fetchall()
            node_ids = self.database.conn.execute("SELECT stable_id,qualified_name,start_line,end_line FROM nodes WHERE project_id=? AND generation_id=? ORDER BY stable_id", (pid,generation_id)).fetchall()
            manifest = hashlib.sha256(json.dumps({"files":[(str(r[0]), str(r[1]), int(r[2])) for r in payload], "nodes":[tuple(r) for r in node_ids], "edges":[tuple(r) for r in edges]}, separators=(",", ":")).encode()).hexdigest()
            self.database.conn.execute(
                "UPDATE index_generations SET status='superseded', "
                "superseded_at=CURRENT_TIMESTAMP, query_ready=0, "
                "manifest_sha256=NULL WHERE project_id=? AND status='active'",
                (pid,),
            )
            self.database.conn.execute("UPDATE index_generations SET status='active',completed_at=CURRENT_TIMESTAMP,promoted_at=CURRENT_TIMESTAMP,manifest_sha256=?,query_ready=?,discovered_files=?,indexed_files=?,symbol_count=?,edge_count=?,diagnostic_count=? WHERE id=?", (manifest if query_ready else None,int(query_ready),counts,counts,symbols,len(edges),diagnostics,generation_id))
        return Generation(generation_id,pid,ordinal,"active",bool(query_ready))

    def clone(self, project: int | str) -> Generation:
        """Clone the current ready generation into an immutable staging copy."""
        active = self.active(project)
        if active is None or not active.query_ready:
            raise ValueError("project has no ready active generation")
        new = self.admit(active.project_id)
        try:
            with self.database.transaction():
                files = self.database.conn.execute("SELECT id,stable_id,path,relative_path,identity_path,hash,byte_size FROM files WHERE project_id=? AND generation_id=?", (active.project_id, active.id)).fetchall()
                fmap: dict[int,int] = {}
                for old, stable, path, rel, ident, digest, size in files:
                    cur = self.database.conn.execute("INSERT INTO files(project_id,generation_id,stable_id,path,relative_path,identity_path,hash,byte_size,nonblank_lines) SELECT project_id,?,stable_id,path,relative_path,identity_path,hash,byte_size,nonblank_lines FROM files WHERE id=?", (new.id,old))
                    fmap[int(old)] = int(cur.lastrowid or 0)
                nodes = self.database.conn.execute("SELECT id,file_id,stable_id,language,type,name,qualified_name,signature,overload_discriminator,start_line,end_line FROM nodes WHERE project_id=? AND generation_id=?", (active.project_id,active.id)).fetchall()
                nmap: dict[int,int] = {}
                for old, file_id, *vals in nodes:
                    cur = self.database.conn.execute("INSERT INTO nodes(project_id,generation_id,file_id,stable_id,language,type,name,qualified_name,signature,overload_discriminator,start_line,end_line) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (active.project_id,new.id,fmap[int(file_id)],*vals))
                    nmap[int(old)] = int(cur.lastrowid or 0)
                for source,target,kind in self.database.conn.execute("SELECT source_id,target_id,type FROM edges WHERE project_id=? AND generation_id=?", (active.project_id,active.id)):
                    self.database.conn.execute("INSERT INTO edges(project_id,generation_id,source_id,target_id,type) VALUES(?,?,?,?,?)", (active.project_id,new.id,nmap[int(source)],nmap[int(target)],kind))
                for node_id, vector, content_hash, model_revision in self.database.conn.execute("SELECT node_id,vector,content_hash,model_revision FROM embeddings WHERE project_id=? AND generation_id=?", (active.project_id,active.id)):
                    self.database.conn.execute("INSERT INTO embeddings(project_id,generation_id,node_id,vector,content_hash,model_revision) VALUES(?,?,?,?,?,?)", (active.project_id,new.id,nmap[int(node_id)],vector,content_hash,model_revision))
                for rel, severity, code, message, exc_type in self.database.conn.execute("SELECT relative_path,severity,code,message,exception_type FROM index_diagnostics WHERE project_id=? AND generation_id=?", (active.project_id,active.id)):
                    self.database.conn.execute("INSERT INTO index_diagnostics(project_id,generation_id,relative_path,severity,code,message,exception_type) VALUES(?,?,?,?,?,?,?)", (active.project_id,new.id,rel,severity,code,message,exc_type))
                for path, kind in self.database.conn.execute("SELECT relative_path,kind FROM untracked_paths WHERE project_id=? AND generation_id=?", (active.project_id,active.id)):
                    self.database.conn.execute("INSERT INTO untracked_paths(project_id,generation_id,relative_path,kind) VALUES(?,?,?,?)", (active.project_id,new.id,path,kind))
                self.database.conn.execute("UPDATE index_generations SET discovered_files=?,indexed_files=?,symbol_count=?,edge_count=? WHERE id=?", (len(files),len(files),len(nodes),self.database.conn.execute("SELECT COUNT(*) FROM edges WHERE generation_id=?", (new.id,)).fetchone()[0],new.id))
                self.database.conn.execute("UPDATE index_generations SET diagnostic_count=(SELECT COUNT(*) FROM index_diagnostics WHERE generation_id=?) WHERE id=?", (new.id,new.id))
        except Exception:
            self.fail(new.id, "clone_failed")
            raise
        return new

    def fail(self, generation_id: int, code: str = "index_failed") -> None:
        self._finish(generation_id, "failed", code)

    def cancel(self, generation_id: int, code: str = "index_cancelled") -> None:
        self._finish(generation_id, "cancelled", code)

    def _finish(self, gid: int, status: str, code: str) -> None:
        with self.database.transaction():
            cur = self.database.conn.execute("UPDATE index_generations SET status=?,completed_at=CURRENT_TIMESTAMP,failure_code=? WHERE id=? AND status='staging'", (status,code,gid))
            if cur.rowcount != 1:
                raise ValueError("generation is not staging")
            row = self.database.conn.execute("SELECT project_id FROM index_generations WHERE id=?", (gid,)).fetchone()
            if row is not None:
                self._diagnostic(int(row[0]), gid, IndexDiagnostic(DiagnosticSeverity.ERROR, code, code))

    def active(self, project: int | str) -> Generation | None:
        pid = self._project(project)
        row = self.database.conn.execute("SELECT id,project_id,ordinal,status,query_ready FROM index_generations WHERE project_id=? AND status='active'", (pid,)).fetchone()
        return None if row is None else Generation(int(row[0]),int(row[1]),int(row[2]),str(row[3]),bool(row[4]))


# Descriptive alias used by callers that treat generation lifecycle separately.
GenerationRepository = IndexRepository


def _nonblank_lines(content: bytes) -> int:
    return sum(1 for line in content.decode("utf-8", errors="replace").splitlines() if line.strip())
