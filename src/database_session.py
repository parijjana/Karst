from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from src.database import Database
from src.security import SecurityViolation, security_error, validate_project_name
from src.settings import TRUSTED_LOCAL_OWNER


@contextmanager
def database_session(factory: Callable[[], Database]) -> Iterator[Database]:
    database = factory()
    try:
        yield database
    finally:
        database.close()


def get_project_id(database: Database, project_name: str) -> int:
    try:
        validate_project_name(project_name)
    except SecurityViolation as error:
        raise ValueError(security_error(error)) from error
    row = database.conn.execute(
        "SELECT id FROM projects WHERE name = ? AND owner = ?",
        (project_name, TRUSTED_LOCAL_OWNER),
    ).fetchone()
    if row is None:
        raise ValueError("Project not found.")
    return int(row[0])
