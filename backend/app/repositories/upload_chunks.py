import sqlite3
from typing import Any


class UploadChunkError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def get_chunk(
    conn: sqlite3.Connection,
    session_id: str,
    chunk_index: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM upload_chunks
        WHERE session_id = ? AND chunk_index = ?
        """,
        (session_id, chunk_index),
    ).fetchone()
    return dict(row) if row is not None else None


def list_verified_chunks(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM upload_chunks
        WHERE session_id = ? AND status = 'verified'
        ORDER BY chunk_index ASC
        """,
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def record_verified_chunk(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    chunk_index: int,
    start_offset: int,
    end_offset: int,
    size_bytes: int,
    sha256: str,
) -> tuple[dict[str, Any], bool]:
    """Record a verified chunk while the caller holds the SQLite write lock."""
    existing = get_chunk(conn, session_id, chunk_index)
    if existing is not None:
        comparable = {
            "start_offset": start_offset,
            "end_offset": end_offset,
            "size_bytes": size_bytes,
            "sha256": sha256,
        }
        if any(existing[key] != value for key, value in comparable.items()):
            raise UploadChunkError("chunk_conflict")
        return existing, False

    cursor = conn.execute(
        """
        INSERT INTO upload_chunks (
            session_id, chunk_index, start_offset, end_offset, size_bytes, sha256, status
        ) VALUES (?, ?, ?, ?, ?, ?, 'verified')
        """,
        (session_id, chunk_index, start_offset, end_offset, size_bytes, sha256),
    )
    row = conn.execute("SELECT * FROM upload_chunks WHERE id = ?", (cursor.lastrowid,)).fetchone()
    if row is None:
        raise RuntimeError("verified upload chunk could not be loaded")
    return dict(row), True
