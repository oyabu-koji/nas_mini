import sqlite3
from typing import Any


TRANSFER_STATUS_UPLOADED = "uploaded"
VERIFICATION_STATUS_SERVER_HASH_RECORDED = "server_hash_recorded"
PREVIEW_STATUS_PREVIEW_GENERATING = "preview_generating"
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
