from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Literal


class ClosingConnection(sqlite3.Connection):
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        try:
            super().__exit__(exc_type, exc_value, traceback)
            return False
        finally:
            self.close()


def connect_sqlite(
    path: Path,
    *,
    readonly: bool = False,
    wal: bool = True,
    busy_timeout_ms: int = 60_000,
) -> sqlite3.Connection:
    resolved = path.resolve()
    if readonly:
        connection = sqlite3.connect(
            f"file:///{resolved.as_posix()}?mode=ro",
            uri=True,
            timeout=busy_timeout_ms / 1000,
            factory=ClosingConnection,
        )
    else:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            resolved, timeout=busy_timeout_ms / 1000, factory=ClosingConnection
        )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("PRAGMA cache_size=-131072")
    connection.execute("PRAGMA mmap_size=268435456")
    if not readonly:
        if wal:
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA wal_autocheckpoint=10000")
    return connection


@contextmanager
def write_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def checkpoint(connection: sqlite3.Connection, *, truncate: bool = True) -> tuple[int, int, int]:
    mode = "TRUNCATE" if truncate else "PASSIVE"
    row = connection.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
    if row is None:
        raise RuntimeError("WAL checkpoint returned no result")
    return int(row[0]), int(row[1]), int(row[2])


def integrity_check(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA integrity_check").fetchone()
    if row is None:
        raise RuntimeError("SQLite integrity_check returned no result")
    return str(row[0])
