import re
import sqlite3
from typing import Any
from uuid import uuid4


RESULT_STATUS_READY = "ready"
RESULT_STATUS_FAILED = "failed"
RESULT_STATUS_SUPERSEDED = "superseded"
RESULT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class ProcessedResultError(ValueError):
    pass


def generate_result_id() -> str:
    return uuid4().hex


def get_processed_result(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    result_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM processed_results
        WHERE asset_id = ? AND id = ?
        """,
        (asset_id, result_id),
    ).fetchone()
    return dict(row) if row is not None else None


def get_processed_result_by_derived_file(
    conn: sqlite3.Connection,
    *,
    derived_file_id: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM processed_results WHERE derived_file_id = ?",
        (derived_file_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def get_active_processed_result(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT processed_results.*
        FROM assets
        JOIN processed_results ON processed_results.id = assets.active_processed_result_id
        WHERE assets.id = ?
        """,
        (asset_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def get_phase2a_backfill_candidate(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    row = conn.execute(
        """
        SELECT
            assets.*,
            derived_files.id AS derived_file_id,
            derived_files.asset_id AS derived_file_asset_id,
            derived_files.kind AS derived_file_kind,
            derived_files.path AS derived_file_path,
            derived_files.mime_type AS derived_file_mime_type,
            derived_files.size_bytes AS derived_file_size_bytes,
            derived_files.created_at AS derived_file_created_at
        FROM assets
        JOIN upload_sessions
          ON upload_sessions.asset_id = assets.id
         AND upload_sessions.status = 'completed'
        JOIN derived_files
          ON derived_files.asset_id = assets.id
         AND derived_files.kind = 'preview'
         AND derived_files.id = (
             SELECT latest_preview.id
             FROM derived_files AS latest_preview
             WHERE latest_preview.asset_id = assets.id
               AND latest_preview.kind = 'preview'
             ORDER BY latest_preview.created_at DESC, latest_preview.id DESC
             LIMIT 1
         )
        WHERE assets.id = ?
          AND assets.type = 'video'
          AND assets.verification_status = 'file_verified'
          AND assets.preview_status = 'preview_ready'
          AND assets.is_log = 0
        """,
        (asset_id,),
    ).fetchone()
    return _backfill_candidate_from_row(row)


def list_phase2a_backfill_candidates(
    conn: sqlite3.Connection,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT
            assets.*,
            derived_files.id AS derived_file_id,
            derived_files.asset_id AS derived_file_asset_id,
            derived_files.kind AS derived_file_kind,
            derived_files.path AS derived_file_path,
            derived_files.mime_type AS derived_file_mime_type,
            derived_files.size_bytes AS derived_file_size_bytes,
            derived_files.created_at AS derived_file_created_at
        FROM assets
        JOIN upload_sessions
          ON upload_sessions.asset_id = assets.id
         AND upload_sessions.status = 'completed'
        JOIN derived_files
          ON derived_files.asset_id = assets.id
         AND derived_files.kind = 'preview'
         AND derived_files.id = (
             SELECT latest_preview.id
             FROM derived_files AS latest_preview
             WHERE latest_preview.asset_id = assets.id
               AND latest_preview.kind = 'preview'
             ORDER BY latest_preview.created_at DESC, latest_preview.id DESC
             LIMIT 1
         )
        WHERE assets.type = 'video'
          AND assets.verification_status = 'file_verified'
          AND assets.preview_status = 'preview_ready'
          AND assets.is_log = 0
        ORDER BY assets.id ASC
        """
    ).fetchall()
    return [candidate for row in rows if (candidate := _backfill_candidate_from_row(row)) is not None]


def is_phase2a_session_video_asset(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM assets
        JOIN upload_sessions
          ON upload_sessions.asset_id = assets.id
         AND upload_sessions.status = 'completed'
        WHERE assets.id = ?
          AND assets.type = 'video'
          AND assets.verification_status = 'file_verified'
          AND assets.is_log = 0
        LIMIT 1
        """,
        (asset_id,),
    ).fetchone()
    return row is not None


def insert_ready_processed_result(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    derived_file_id: int,
    mime_type: str,
    size_bytes: int,
    sha256: str,
    preview_generation: int | None = None,
    result_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Insert an immutable ready result without committing the caller transaction."""
    assigned_result_id = result_id or generate_result_id()
    _validate_ready_result_input(
        result_id=assigned_result_id,
        mime_type=mime_type,
        size_bytes=size_bytes,
        sha256=sha256,
        preview_generation=preview_generation,
    )

    existing_by_derived_file = get_processed_result_by_derived_file(
        conn,
        derived_file_id=derived_file_id,
    )
    if existing_by_derived_file is not None:
        if _matches_ready_identity(
            existing_by_derived_file,
            result_id=assigned_result_id,
            asset_id=asset_id,
            derived_file_id=derived_file_id,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            preview_generation=preview_generation,
        ):
            return existing_by_derived_file, False
        raise ProcessedResultError("processed result already exists for derived file")

    existing_by_id = conn.execute(
        "SELECT * FROM processed_results WHERE id = ?",
        (assigned_result_id,),
    ).fetchone()
    if existing_by_id is not None:
        existing = dict(existing_by_id)
        if _matches_ready_identity(
            existing,
            result_id=assigned_result_id,
            asset_id=asset_id,
            derived_file_id=derived_file_id,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            preview_generation=preview_generation,
        ):
            return existing, False
        raise ProcessedResultError("processed result ID conflicts with an existing result")

    cursor = conn.execute(
        """
        INSERT INTO processed_results (
            id, asset_id, derived_file_id, status, mime_type, size_bytes, sha256,
            preview_generation
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assigned_result_id,
            asset_id,
            derived_file_id,
            RESULT_STATUS_READY,
            mime_type,
            size_bytes,
            sha256,
            preview_generation,
        ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("processed result could not be inserted")
    inserted = get_processed_result(conn, asset_id=asset_id, result_id=assigned_result_id)
    if inserted is None:
        raise RuntimeError("inserted processed result could not be loaded")
    return inserted, True


def set_active_processed_result(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    result_id: str,
) -> None:
    _validate_result_id(result_id)
    cursor = conn.execute(
        """
        UPDATE assets
        SET active_processed_result_id = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (result_id, asset_id),
    )
    if cursor.rowcount != 1:
        raise ProcessedResultError("asset not found")


def clear_active_processed_result(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
) -> None:
    cursor = conn.execute(
        """
        UPDATE assets
        SET active_processed_result_id = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (asset_id,),
    )
    if cursor.rowcount != 1:
        raise ProcessedResultError("asset not found")


def _validate_ready_result_input(
    *,
    result_id: str,
    mime_type: str,
    size_bytes: int,
    sha256: str,
    preview_generation: int | None,
) -> None:
    _validate_result_id(result_id)
    if not mime_type.startswith("video/"):
        raise ProcessedResultError("processed result MIME type must be video")
    if size_bytes <= 0:
        raise ProcessedResultError("processed result size must be positive")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ProcessedResultError("processed result SHA-256 must be lowercase hexadecimal")
    if preview_generation is not None and preview_generation < 0:
        raise ProcessedResultError("preview generation must be non-negative")


def _validate_result_id(result_id: str) -> None:
    if not RESULT_ID_PATTERN.fullmatch(result_id):
        raise ProcessedResultError("processed result ID must be lowercase UUID hex")


def _matches_ready_identity(
    existing: dict[str, Any],
    *,
    result_id: str,
    asset_id: int,
    derived_file_id: int,
    mime_type: str,
    size_bytes: int,
    sha256: str,
    preview_generation: int | None,
) -> bool:
    return (
        existing["id"] == result_id
        and existing["asset_id"] == asset_id
        and existing["derived_file_id"] == derived_file_id
        and existing["status"] == RESULT_STATUS_READY
        and existing["mime_type"] == mime_type
        and existing["size_bytes"] == size_bytes
        and existing["sha256"] == sha256
        and existing["preview_generation"] == preview_generation
    )


def _backfill_candidate_from_row(
    row: sqlite3.Row | None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if row is None:
        return None
    values = dict(row)
    derived_file = {
        "id": values.pop("derived_file_id"),
        "asset_id": values.pop("derived_file_asset_id"),
        "kind": values.pop("derived_file_kind"),
        "path": values.pop("derived_file_path"),
        "mime_type": values.pop("derived_file_mime_type"),
        "size_bytes": values.pop("derived_file_size_bytes"),
        "created_at": values.pop("derived_file_created_at"),
    }
    return values, derived_file
