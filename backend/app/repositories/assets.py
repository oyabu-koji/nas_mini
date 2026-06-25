import sqlite3
from typing import Any


TRANSFER_STATUS_UPLOADED = "uploaded"
VERIFICATION_STATUS_SERVER_HASH_RECORDED = "server_hash_recorded"
PREVIEW_STATUS_PREVIEW_GENERATING = "preview_generating"
PREVIEW_STATUS_PREVIEW_READY = "preview_ready"
PREVIEW_STATUS_FAILED = "failed"
REVIEW_STATUS_NOT_REVIEWED = "not_reviewed"
DELETE_CANDIDATE_STATUS_NOT_CANDIDATE = "not_candidate"


def insert_asset(
    conn: sqlite3.Connection,
    *,
    type: str,
    filename: str,
    original_path: str,
    size_bytes: int,
    server_sha256: str,
    taken_at: str | None,
    latitude: float | None,
    longitude: float | None,
    exif_json: str | None,
    is_log: bool,
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        INSERT INTO assets (
            type,
            filename,
            original_path,
            size_bytes,
            server_sha256,
            taken_at,
            latitude,
            longitude,
            exif_json,
            is_log,
            transfer_status,
            verification_status,
            preview_status,
            review_status,
            delete_candidate_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            type,
            filename,
            original_path,
            size_bytes,
            server_sha256,
            taken_at,
            latitude,
            longitude,
            exif_json,
            1 if is_log else 0,
            TRANSFER_STATUS_UPLOADED,
            VERIFICATION_STATUS_SERVER_HASH_RECORDED,
            PREVIEW_STATUS_PREVIEW_GENERATING,
            REVIEW_STATUS_NOT_REVIEWED,
            DELETE_CANDIDATE_STATUS_NOT_CANDIDATE,
        ),
    )
    row = conn.execute(
        "SELECT * FROM assets WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    if row is None:
        raise RuntimeError("inserted asset could not be loaded")
    return dict(row)


def get_asset(conn: sqlite3.Connection, asset_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return dict(row) if row is not None else None


def update_preview_status(
    conn: sqlite3.Connection,
    asset_id: int,
    preview_status: str,
) -> None:
    if preview_status not in {PREVIEW_STATUS_PREVIEW_READY, PREVIEW_STATUS_FAILED}:
        raise ValueError("unsupported preview status")

    conn.execute(
        """
        UPDATE assets
        SET preview_status = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (preview_status, asset_id),
    )
