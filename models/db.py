"""Shared SQLite connection helper — part of the frozen contract (see
FILE-TREE.md's "Adjustments made this phase" and interfaces/README.md's
store_raw_emails note). Both ingestion/store.py and pipeline/persist.py
need SQLite connection setup + schema DDL; this avoids each hand-rolling
its own (DRY, per CLAUDE.md's development practices).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


@contextmanager
def connect(db_path: Path, schema_ddl: Optional[str] = None) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection at db_path, creating its parent directory
    (if any) and applying schema_ddl (if given) before yielding."""
    path = Path(db_path)
    if str(path) != ":memory:" and path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        if schema_ddl:
            conn.executescript(schema_ddl)
            conn.commit()
        yield conn
    finally:
        conn.close()
