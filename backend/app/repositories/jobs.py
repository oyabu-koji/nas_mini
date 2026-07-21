import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import uuid4


SUPPORTED_JOB_TYPES: set[str] = {"preview", "lut_preview", "upload_finalize"}
MAX_ERROR_MESSAGE_LENGTH = 200


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def insert_job(
    conn: sqlite3.Connection,
    *,
    job_type: str,
    asset_id: int | None,
    payload_json: str,
    dedup_key: str | None = None,
) -> dict[str, Any]:
    if not _has_dedup_key_column(conn):
        cursor = conn.execute(
            """
            INSERT INTO jobs (job_type, status, asset_id, payload_json)
            VALUES (?, 'queued', ?, ?)
            """,
            (job_type, asset_id, payload_json),
        )
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (cursor.lastrowid,)).fetchone()
        if row is None:
            raise RuntimeError("inserted legacy job could not be loaded")
        return dict(row)
    job, _ = insert_or_return_job(
        conn,
        job_type=job_type,
        asset_id=asset_id,
        payload_json=payload_json,
        dedup_key=dedup_key or f"adhoc:{uuid4().hex}",
    )
    return job


def insert_or_return_job(
    conn: sqlite3.Connection,
    *,
    job_type: str,
    asset_id: int | None,
    payload_json: str,
    dedup_key: str,
) -> tuple[dict[str, Any], bool]:
    if not dedup_key:
        raise ValueError("dedup key is required")

    existing = get_job_by_dedup_key(conn, dedup_key)
    if existing is not None:
        if existing["job_type"] != job_type or existing["asset_id"] != asset_id:
            raise ValueError("dedup key conflicts with a different job")
        return existing, False

    cursor = conn.execute(
        """
        INSERT INTO jobs (job_type, status, asset_id, payload_json, dedup_key)
        VALUES (?, 'queued', ?, ?, ?)
        """,
        (job_type, asset_id, payload_json, dedup_key),
    )
    row = conn.execute(
        "SELECT * FROM jobs WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    if row is None:
        raise RuntimeError("inserted job could not be loaded")
    return dict(row), True


def get_job_by_dedup_key(conn: sqlite3.Connection, dedup_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM jobs WHERE dedup_key = ?",
        (dedup_key,),
    ).fetchone()
    return dict(row) if row is not None else None


def _has_dedup_key_column(conn: sqlite3.Connection) -> bool:
    return any(
        row["name"] == "dedup_key"
        for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
    )


def requeue_retryable_finalize_job(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    cursor = conn.execute(
        """
        UPDATE jobs
        SET status = 'queued', error_message = NULL, claimed_at = NULL,
            lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND job_type = 'upload_finalize' AND status = 'failed'
        """,
        (job_id,),
    )
    if cursor.rowcount != 1:
        raise ValueError("finalization job is not retryable")
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise RuntimeError("requeued job could not be loaded")
    return dict(row)


def recover_expired_jobs(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    current_time = isoformat(now or utc_now())
    with conn:
        cursor = conn.execute(
            """
            UPDATE jobs
            SET status = 'queued',
                claimed_at = NULL,
                lease_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (current_time,),
        )
    return cursor.rowcount


def claim_next_job(
    conn: sqlite3.Connection,
    lease_seconds: int,
    supported_job_types: Iterable[str],
    now: datetime | None = None,
) -> dict[str, Any] | None:
    supported_types = tuple(sorted(set(supported_job_types)))
    if not supported_types:
        return None

    claimed_at = now or utc_now()
    lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
    placeholders = ", ".join("?" for _ in supported_types)

    with conn:
        row = conn.execute(
            f"""
            SELECT *
            FROM jobs
            WHERE status = 'queued'
              AND job_type IN ({placeholders})
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            supported_types,
        ).fetchone()
        if row is None:
            return None

        cursor = conn.execute(
            f"""
            UPDATE jobs
            SET status = 'running',
                claimed_at = ?,
                lease_expires_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status = 'queued'
              AND job_type IN ({placeholders})
            """,
            (
                isoformat(claimed_at),
                isoformat(lease_expires_at),
                row["id"],
                *supported_types,
            ),
        )
        if cursor.rowcount != 1:
            return None

        claimed = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()

    return dict(claimed) if claimed is not None else None


def mark_job_failed(
    conn: sqlite3.Connection,
    job_id: int,
    error_message: str,
) -> None:
    with conn:
        set_job_failed_in_transaction(conn, job_id=job_id, error_message=error_message)


def fail_job_and_asset_preview(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    asset_id: int | None,
    error_message: str,
) -> None:
    """Terminally fail a preview job and its existing target asset together."""
    with conn:
        fail_job_and_asset_preview_in_transaction(
            conn,
            job_id=job_id,
            asset_id=asset_id,
            error_message=error_message,
        )


def mark_job_done(conn: sqlite3.Connection, job_id: int) -> None:
    with conn:
        set_job_done_in_transaction(conn, job_id=job_id)


def set_job_failed_in_transaction(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    error_message: str,
) -> None:
    sanitized_error = error_message[:MAX_ERROR_MESSAGE_LENGTH]
    conn.execute(
        """
        UPDATE jobs
        SET status = 'failed',
            error_message = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (sanitized_error, job_id),
    )


def fail_job_and_asset_preview_in_transaction(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    asset_id: int | None,
    error_message: str,
) -> None:
    if asset_id is not None:
        conn.execute(
            """
            UPDATE assets
            SET preview_status = 'failed',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (asset_id,),
        )
    set_job_failed_in_transaction(conn, job_id=job_id, error_message=error_message)


def set_job_done_in_transaction(conn: sqlite3.Connection, *, job_id: int) -> None:
    conn.execute(
        """
        UPDATE jobs
        SET status = 'done',
            error_message = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (job_id,),
    )


def fail_unsupported_job(conn: sqlite3.Connection, job: dict[str, Any]) -> None:
    mark_job_failed(conn, job["id"], f"Unsupported job type: {job['job_type']}")
