import sqlite3
from pathlib import Path


def connect(database_path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def connect_read_only(database_path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
    """Open an existing immutable SQLite database without creating sidecars."""
    if busy_timeout_ms <= 0:
        raise sqlite3.OperationalError("invalid busy timeout")
    if database_path.is_symlink() or not database_path.is_file():
        raise sqlite3.OperationalError("database must be an existing regular file")
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{database_path}{suffix}")
        if sidecar.exists() or sidecar.is_symlink():
            raise sqlite3.OperationalError("database sidecars must be absent")
    resolved = database_path.resolve(strict=True)
    # Sidecars are rejected above, so immutable mode cannot hide committed WAL data.
    # It prevents SQLite from creating a new WAL index for a standalone backup whose
    # database header still records WAL journal mode.
    uri = f"{resolved.as_uri()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
