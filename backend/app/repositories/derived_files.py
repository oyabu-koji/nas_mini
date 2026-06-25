import sqlite3
from typing import Any


DERIVED_KIND_PREVIEW = "preview"


def get_preview_for_asset(conn: sqlite3.Connection, asset_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM derived_files
        WHERE asset_id = ?
          AND kind = ?
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """,
        (asset_id, DERIVED_KIND_PREVIEW),
    ).fetchone()
    return dict(row) if row is not None else None


def insert_derived_file(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    kind: str,
    path: str,
    mime_type: str,
    size_bytes: int,
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        INSERT INTO derived_files (asset_id, kind, path, mime_type, size_bytes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (asset_id, kind, path, mime_type, size_bytes),
    )
    row = conn.execute(
        "SELECT * FROM derived_files WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    if row is None:
        raise RuntimeError("inserted derived file could not be loaded")
    return dict(row)
