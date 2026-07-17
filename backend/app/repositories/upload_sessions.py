import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any


EXPIRABLE_STATUSES = {"created", "uploading", "ready_to_finalize"}
UPLOADABLE_STATUSES = {"created", "uploading"}
CANCELLABLE_STATUSES = {"created", "uploading", "ready_to_finalize", "failed"}


class UploadSessionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def create_or_return_session(
    conn: sqlite3.Connection,
    *,
    session: dict[str, Any],
    active_limit: int,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """Create a new session or return the existing idempotency-key session.

    The existing key lookup intentionally precedes active-session enforcement so a
    lost create response is always recoverable while the session remains valid.
    """
    current_time = now or utc_now()
    current_time_text = isoformat(current_time)

    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = _get_by_client_upload_id(conn, session["client_upload_id"])
        if existing is not None:
            existing = _expire_if_due(conn, existing, current_time_text)
            if not _same_immutable_metadata(existing, session):
                raise UploadSessionError("session_metadata_conflict")
            if existing["status"] in {"expired", "cancelled"}:
                raise UploadSessionError(f"session_{existing['status']}")
            conn.commit()
            return existing, False

        _expire_due_sessions(conn, current_time_text)
        active_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM upload_sessions
            WHERE status IN ('created', 'uploading', 'ready_to_finalize', 'assembling')
               OR (status = 'failed' AND retryable = 1)
            """
        ).fetchone()["count"]
        if active_count >= active_limit:
            raise UploadSessionError("active_session_limit")

        conn.execute(
            """
            INSERT INTO upload_sessions (
                id, client_upload_id, type, filename, size_bytes,
                expected_file_sha256, chunk_size_bytes, original_relative_path,
                taken_at, latitude, longitude, exif_json, is_log, status,
                last_activity_at, expires_at
            ) VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)
            """,
            (
                session["id"],
                session["client_upload_id"],
                session["filename"],
                session["size_bytes"],
                session["expected_file_sha256"],
                session["chunk_size_bytes"],
                session["original_relative_path"],
                session.get("taken_at"),
                session.get("latitude"),
                session.get("longitude"),
                session.get("exif_json"),
                1 if session.get("is_log") else 0,
                current_time_text,
                session["expires_at"],
            ),
        )
        created = get_session(conn, session["id"])
        if created is None:
            raise RuntimeError("created upload session could not be loaded")
        conn.commit()
        return created, True
    except Exception:
        conn.rollback()
        raise


def get_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM upload_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def get_session_by_client_upload_id(
    conn: sqlite3.Connection,
    client_upload_id: str,
) -> dict[str, Any] | None:
    return _get_by_client_upload_id(conn, client_upload_id)


def get_session_or_expire(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    current_time_text = isoformat(now or utc_now())
    conn.execute("BEGIN IMMEDIATE")
    try:
        session = get_session(conn, session_id)
        if session is not None:
            session = _expire_if_due(conn, session, current_time_text)
        conn.commit()
        return session
    except Exception:
        conn.rollback()
        raise


def mark_cancelled(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time_text = isoformat(now or utc_now())
    conn.execute("BEGIN IMMEDIATE")
    try:
        session = get_session(conn, session_id)
        if session is None:
            raise UploadSessionError("session_not_found")
        session = _expire_if_due(conn, session, current_time_text)
        if session["status"] == "expired":
            raise UploadSessionError("session_expired")
        if session["status"] == "failed" and not session["retryable"]:
            raise UploadSessionError("session_not_cancellable")
        if session["status"] not in CANCELLABLE_STATUSES:
            raise UploadSessionError("session_not_cancellable")
        conn.execute(
            """
            UPDATE upload_sessions
            SET status = 'cancelled', retryable = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (session_id,),
        )
        cancelled = get_session(conn, session_id)
        conn.commit()
        if cancelled is None:
            raise RuntimeError("cancelled upload session could not be loaded")
        return cancelled
    except Exception:
        conn.rollback()
        raise


def touch_session_activity(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    expiry_seconds: int,
    now: datetime | None = None,
) -> None:
    current_time = now or utc_now()
    conn.execute(
        """
        UPDATE upload_sessions
        SET last_activity_at = ?, expires_at = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (isoformat(current_time), isoformat(current_time + timedelta(seconds=expiry_seconds)), session_id),
    )


def get_uploadable_session_for_chunk(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    current_time_text: str,
) -> dict[str, Any]:
    """Return an uploadable session while the caller holds BEGIN IMMEDIATE."""
    session = get_session(conn, session_id)
    if session is None:
        raise UploadSessionError("session_not_found")
    session = _expire_if_due(conn, session, current_time_text)
    if session["status"] == "expired":
        raise UploadSessionError("session_expired")
    if session["status"] not in UPLOADABLE_STATUSES:
        raise UploadSessionError("session_not_uploadable")
    return session


def get_session_for_transition(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    current_time_text: str,
) -> dict[str, Any]:
    """Load a session and apply inactivity expiry while the caller owns the write lock."""
    session = get_session(conn, session_id)
    if session is None:
        raise UploadSessionError("session_not_found")
    session = _expire_if_due(conn, session, current_time_text)
    if session["status"] == "expired":
        raise UploadSessionError("session_expired")
    return session


def mark_ready_to_finalize_if_complete(conn: sqlite3.Connection, session_id: str) -> None:
    session = get_session(conn, session_id)
    if session is None:
        raise UploadSessionError("session_not_found")
    expected_chunks = (session["size_bytes"] + session["chunk_size_bytes"] - 1) // session["chunk_size_bytes"]
    count = conn.execute(
        "SELECT COUNT(*) AS count FROM upload_chunks WHERE session_id = ? AND status = 'verified'",
        (session_id,),
    ).fetchone()["count"]
    if count == expected_chunks:
        conn.execute(
            """
            UPDATE upload_sessions
            SET status = 'ready_to_finalize', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('created', 'uploading')
            """,
            (session_id,),
        )


def _get_by_client_upload_id(conn: sqlite3.Connection, client_upload_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM upload_sessions WHERE client_upload_id = ?",
        (client_upload_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _same_immutable_metadata(existing: dict[str, Any], requested: dict[str, Any]) -> bool:
    fields = (
        "filename",
        "size_bytes",
        "expected_file_sha256",
        "chunk_size_bytes",
        "taken_at",
        "latitude",
        "longitude",
        "exif_json",
    )
    return all(existing[field] == requested.get(field) for field in fields) and bool(existing["is_log"]) == bool(requested.get("is_log"))


def _expire_due_sessions(conn: sqlite3.Connection, current_time_text: str) -> None:
    conn.execute(
        """
        UPDATE upload_sessions
        SET status = 'expired', retryable = 0, updated_at = CURRENT_TIMESTAMP
        WHERE expires_at <= ?
          AND (
              status IN ('created', 'uploading', 'ready_to_finalize')
              OR (status = 'failed' AND retryable = 1)
          )
        """,
        (current_time_text,),
    )


def _expire_if_due(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    current_time_text: str,
) -> dict[str, Any]:
    if (
        session["expires_at"] <= current_time_text
        and (session["status"] in EXPIRABLE_STATUSES or (session["status"] == "failed" and session["retryable"]))
    ):
        conn.execute(
            """
            UPDATE upload_sessions
            SET status = 'expired', retryable = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (session["id"],),
        )
        expired = get_session(conn, session["id"])
        if expired is None:
            raise RuntimeError("expired upload session could not be loaded")
        return expired
    return session
