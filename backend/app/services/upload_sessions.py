import json
import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.upload_chunks import (
    UploadChunkError,
    get_chunk,
    list_verified_chunks,
    record_verified_chunk,
)
from app.repositories.upload_sessions import (
    UploadSessionError,
    create_or_return_session,
    get_session,
    get_session_by_client_upload_id,
    get_session_for_transition,
    get_uploadable_session_for_chunk,
    get_session_or_expire,
    isoformat,
    mark_ready_to_finalize_if_complete,
    touch_session_activity,
    utc_now,
    mark_cancelled,
)
from app.repositories.jobs import (
    get_job_by_dedup_key,
    insert_or_return_job,
    requeue_retryable_finalize_job,
)
from app.schemas.assets import exif_json_to_text
from app.schemas.upload_sessions import UploadSessionCreateRequest
from app.services.storage import (
    cleanup_session_temporary_files,
    generate_session_chunk_path,
    generate_session_original_relative_path,
    generate_session_staging_path,
)


class UploadSessionServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


def create_upload_session(
    *,
    settings: Settings,
    request: UploadSessionCreateRequest,
) -> tuple[dict[str, Any], bool]:
    if request.size_bytes > settings.upload_session_max_size_bytes:
        raise UploadSessionServiceError("session_size_limit")

    now = utc_now()
    session_id = str(uuid4())
    session_data = {
        "id": session_id,
        "client_upload_id": request.client_upload_id,
        "filename": request.filename,
        "size_bytes": request.size_bytes,
        "expected_file_sha256": request.expected_file_sha256,
        "chunk_size_bytes": settings.upload_session_chunk_size_bytes,
        "original_relative_path": generate_session_original_relative_path(session_id, request.filename),
        "taken_at": request.taken_at,
        "latitude": request.latitude,
        "longitude": request.longitude,
        "exif_json": exif_json_to_text(request.exif_json),
        "is_log": request.is_log,
        "expires_at": isoformat(now + timedelta(seconds=settings.upload_session_expiry_seconds)),
    }

    try:
        with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
            return create_or_return_session(
                conn,
                session=session_data,
                active_limit=settings.upload_session_active_limit,
                now=now,
            )
    except UploadSessionError as exc:
        if exc.code == "session_expired":
            with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
                expired = get_session_by_client_upload_id(conn, request.client_upload_id)
            if expired is not None:
                cleanup_session_temporary_files(settings.media_root, expired["id"])
        raise UploadSessionServiceError(
            exc.code,
            retryable=exc.code == "active_session_limit",
            retry_after_seconds=(
                settings.upload_session_retry_after_seconds
                if exc.code == "active_session_limit"
                else None
            ),
        ) from exc


def get_upload_session(*, settings: Settings, session_id: str) -> dict[str, Any]:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        session = get_session_or_expire(conn, session_id)
        if session is None:
            raise UploadSessionServiceError("session_not_found")
        if session["status"] == "expired":
            cleanup_session_temporary_files(settings.media_root, session_id)
            raise UploadSessionServiceError("session_expired")
        verified_chunks = list_verified_chunks(conn, session_id)
    return _session_response(session, verified_chunks)


async def upload_session_chunk(
    *,
    settings: Settings,
    session_id: str,
    chunk_index: int,
    content_range: tuple[int, int, int],
    expected_chunk_sha256: str,
    body_stream: Any,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Persist one raw chunk without placing its bytes in process memory."""
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        session = get_session(conn, session_id)
    if session is None:
        raise UploadSessionServiceError("session_not_found")

    start_offset, end_offset, total_size = content_range
    _validate_chunk_range(session, chunk_index, start_offset, end_offset, total_size)
    staging_path = generate_session_staging_path(settings.media_root, session_id)
    expected_size = end_offset - start_offset + 1

    try:
        actual_sha256, actual_size = await _save_stream_to_staging(body_stream, staging_path, expected_size)
        if actual_sha256 != expected_chunk_sha256:
            raise UploadSessionServiceError("chunk_hash_mismatch")

        now = utc_now()
        with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = get_uploadable_session_for_chunk(
                    conn,
                    session_id,
                    current_time_text=isoformat(now),
                )
                _validate_chunk_range(current, chunk_index, start_offset, end_offset, total_size)
                existing = get_chunk(conn, session_id, chunk_index)
                if existing is not None:
                    chunk, inserted = record_verified_chunk(
                        conn,
                        session_id=session_id,
                        chunk_index=chunk_index,
                        start_offset=start_offset,
                        end_offset=end_offset,
                        size_bytes=actual_size,
                        sha256=actual_sha256,
                    )
                    touch_session_activity(
                        conn,
                        session_id,
                        expiry_seconds=settings.upload_session_expiry_seconds,
                        now=now,
                    )
                    conn.commit()
                    return current, chunk, inserted

                canonical_path = generate_session_chunk_path(settings.media_root, session_id, chunk_index)
                canonical_path.parent.mkdir(parents=True, exist_ok=True)
                staging_path.replace(canonical_path)
                chunk, inserted = record_verified_chunk(
                    conn,
                    session_id=session_id,
                    chunk_index=chunk_index,
                    start_offset=start_offset,
                    end_offset=end_offset,
                    size_bytes=actual_size,
                    sha256=actual_sha256,
                )
                conn.execute(
                    """
                    UPDATE upload_sessions
                    SET status = 'uploading', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'created'
                    """,
                    (session_id,),
                )
                touch_session_activity(
                    conn,
                    session_id,
                    expiry_seconds=settings.upload_session_expiry_seconds,
                    now=now,
                )
                mark_ready_to_finalize_if_complete(conn, session_id)
                updated = get_session(conn, session_id)
                conn.commit()
                if updated is None:
                    raise RuntimeError("upload session could not be reloaded")
                return updated, chunk, inserted
            except Exception:
                conn.rollback()
                raise
    except UploadSessionError as exc:
        if exc.code == "session_expired":
            cleanup_session_temporary_files(settings.media_root, session_id)
        raise UploadSessionServiceError(exc.code) from exc
    except UploadChunkError as exc:
        raise UploadSessionServiceError(exc.code) from exc
    finally:
        staging_path.unlink(missing_ok=True)


def _validate_chunk_range(
    session: dict[str, Any],
    chunk_index: int,
    start_offset: int,
    end_offset: int,
    total_size: int,
) -> None:
    chunk_size = session["chunk_size_bytes"]
    expected_start = chunk_index * chunk_size
    expected_end = min(expected_start + chunk_size, session["size_bytes"]) - 1
    if (
        chunk_index < 0
        or expected_start >= session["size_bytes"]
        or (start_offset, end_offset, total_size) != (expected_start, expected_end, session["size_bytes"])
    ):
        raise UploadSessionServiceError("chunk_range_invalid")


async def _save_stream_to_staging(body_stream: Any, staging_path: Path, expected_size: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total_size = 0
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with staging_path.open("xb") as output_file:
            async for raw_chunk in body_stream:
                if not raw_chunk:
                    continue
                total_size += len(raw_chunk)
                if total_size > expected_size:
                    raise UploadSessionServiceError("chunk_size_invalid")
                output_file.write(raw_chunk)
                digest.update(raw_chunk)
        if total_size != expected_size:
            raise UploadSessionServiceError("chunk_size_invalid")
        return digest.hexdigest(), total_size
    except Exception:
        staging_path.unlink(missing_ok=True)
        raise


def build_session_response_from_rows(
    session: dict[str, Any],
    verified_chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    return _session_response(session, verified_chunks)


def build_finalize_response(
    *,
    settings: Settings,
    session: dict[str, Any],
    job_id: int | None,
) -> dict[str, Any]:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        verified_chunks = list_verified_chunks(conn, session["id"])
        preview_job = (
            get_job_by_dedup_key(conn, f"initial-preview:{session['asset_id']}")
            if session["asset_id"] is not None
            else None
        )
    return {
        "session": _session_response(session, verified_chunks),
        "job_id": job_id,
        "asset_id": session["asset_id"],
        "preview_job_id": preview_job["id"] if preview_job is not None else None,
    }


def finalize_upload_session(*, settings: Settings, session_id: str) -> tuple[dict[str, Any], int | None]:
    now = utc_now()
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            session = get_session_for_transition(
                conn,
                session_id,
                current_time_text=isoformat(now),
            )
            if session["status"] in {"completed", "assembling"}:
                conn.commit()
                return session, session["finalization_job_id"]
            if session["status"] == "failed":
                if not session["retryable"] or session["finalization_job_id"] is None:
                    raise UploadSessionServiceError("session_terminal_failure")
                job = requeue_retryable_finalize_job(conn, session["finalization_job_id"])
                conn.execute(
                    """
                    UPDATE upload_sessions
                    SET status = 'assembling', retryable = 0, attempt_count = attempt_count + 1,
                        failure_code = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (session_id,),
                )
                touch_session_activity(
                    conn,
                    session_id,
                    expiry_seconds=settings.upload_session_expiry_seconds,
                    now=now,
                )
                updated = get_session(conn, session_id)
                conn.commit()
                if updated is None:
                    raise RuntimeError("requeued session could not be loaded")
                return updated, job["id"]
            if session["status"] not in {"ready_to_finalize", "uploading", "created"}:
                raise UploadSessionServiceError("session_not_finalizable")

            expected_chunks = (session["size_bytes"] + session["chunk_size_bytes"] - 1) // session["chunk_size_bytes"]
            verified_chunks = list_verified_chunks(conn, session_id)
            if len(verified_chunks) != expected_chunks:
                raise UploadSessionServiceError("missing_chunks")
            job, _created = insert_or_return_job(
                conn,
                job_type="upload_finalize",
                asset_id=None,
                payload_json=json.dumps({"session_id": session_id}, separators=(",", ":")),
                dedup_key=f"finalize:{session_id}",
            )
            conn.execute(
                """
                UPDATE upload_sessions
                SET status = 'assembling', finalization_job_id = ?, retryable = 0,
                    attempt_count = attempt_count + 1, failure_code = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job["id"], session_id),
            )
            touch_session_activity(
                conn,
                session_id,
                expiry_seconds=settings.upload_session_expiry_seconds,
                now=now,
            )
            updated = get_session(conn, session_id)
            conn.commit()
            if updated is None:
                raise RuntimeError("finalizing session could not be loaded")
            return updated, job["id"]
        except UploadSessionError as exc:
            conn.rollback()
            if exc.code == "session_expired":
                cleanup_session_temporary_files(settings.media_root, session_id)
            raise UploadSessionServiceError(exc.code) from exc
        except UploadSessionServiceError:
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise


def cancel_upload_session(*, settings: Settings, session_id: str) -> dict[str, Any]:
    try:
        with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
            cancelled = mark_cancelled(conn, session_id)
            verified_chunks = list_verified_chunks(conn, session_id)
    except UploadSessionError as exc:
        if exc.code == "session_expired":
            cleanup_session_temporary_files(settings.media_root, session_id)
        raise UploadSessionServiceError(exc.code) from exc
    cleanup_session_temporary_files(settings.media_root, session_id)
    return _session_response(cancelled, verified_chunks)


def _session_response(
    session: dict[str, Any],
    verified_chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    total_chunks = (session["size_bytes"] + session["chunk_size_bytes"] - 1) // session["chunk_size_bytes"]
    verified_indexes = {chunk["chunk_index"] for chunk in verified_chunks}
    return {
        "id": session["id"],
        "status": session["status"],
        "size_bytes": session["size_bytes"],
        "chunk_size_bytes": session["chunk_size_bytes"],
        "total_chunks": total_chunks,
        "expected_file_sha256": session["expected_file_sha256"],
        "expires_at": session["expires_at"],
        "missing_chunk_indexes": [index for index in range(total_chunks) if index not in verified_indexes],
        "retryable": bool(session["retryable"]),
        "failure_code": session["failure_code"],
        "asset_id": session["asset_id"],
        "finalization_job_id": session["finalization_job_id"],
    }
