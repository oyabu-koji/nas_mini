import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


SUPPORTED_JOB_TYPES: set[str] = set()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def insert_job(
    conn: sqlite3.Connection,
    *,
    job_type: str,
    asset_id: int,
    payload_json: str,
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        INSERT INTO jobs (job_type, status, asset_id, payload_json)
        VALUES (?, 'queued', ?, ?)
        """,
        (job_type, asset_id, payload_json),
    )
    row = conn.execute(
        "SELECT * FROM jobs WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    if row is None:
        raise RuntimeError("inserted job could not be loaded")
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
        conn.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                error_message = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (error_message, job_id),
        )


def fail_unsupported_job(conn: sqlite3.Connection, job: dict[str, Any]) -> None:
    mark_job_failed(conn, job["id"], f"Unsupported job type: {job['job_type']}")
