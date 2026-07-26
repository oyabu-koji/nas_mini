import hashlib
import json
from pathlib import Path
from typing import Any

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.assets import insert_verified_video_asset
from app.repositories.jobs import insert_or_return_job
from app.repositories.upload_chunks import list_verified_chunks
from app.repositories.upload_sessions import get_session
from app.schemas.assets import exif_json_from_text
from app.services.storage import (
    cleanup_session_temporary_files,
    generate_session_assembly_path,
    generate_session_chunk_path,
    resolve_media_path,
)
from app.db.phase2b import has_valid_phase2b_schema


COPY_BLOCK_SIZE_BYTES = 1_048_576


class UploadFinalizeError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def process_upload_finalize_job(*, settings: Settings, job: dict[str, Any]) -> bool:
    """Finalize exactly one claimed upload job without exposing partial data."""
    session_id: str | None = None
    assembly_path: Path | None = None
    try:
        session_id = _session_id_from_job(job)
        with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
            session = get_session(conn, session_id)
        _validate_claimed_session(session, job)
        if session is None:
            raise UploadFinalizeError("session_missing", retryable=False)

        assembly_path = generate_session_assembly_path(settings.media_root, session_id)
        final_path = resolve_media_path(settings.media_root, session["original_relative_path"])
        final_sha256 = _ensure_final_file(
            settings=settings,
            session=session,
            assembly_path=assembly_path,
            final_path=final_path,
        )
        _commit_finalization(
            settings=settings,
            job=job,
            session=session,
            final_sha256=final_sha256,
        )
        cleanup_session_temporary_files(settings.media_root, session_id)
        return True
    except UploadFinalizeError as exc:
        _cleanup_assembly(assembly_path)
        _mark_finalization_failed(
            settings=settings,
            job_id=job["id"],
            session_id=session_id,
            code=exc.code,
            retryable=exc.retryable,
        )
        return True
    except Exception:
        _cleanup_assembly(assembly_path)
        _mark_finalization_failed(
            settings=settings,
            job_id=job["id"],
            session_id=session_id,
            code="finalization_storage_failure",
            retryable=True,
        )
        return True


def _session_id_from_job(job: dict[str, Any]) -> str:
    payload_json = job.get("payload_json")
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise UploadFinalizeError("finalization_payload_invalid", retryable=False) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("session_id"), str):
        raise UploadFinalizeError("finalization_payload_invalid", retryable=False)
    return payload["session_id"]


def _validate_claimed_session(session: dict[str, Any] | None, job: dict[str, Any]) -> None:
    if session is None:
        return
    if session["finalization_job_id"] != job["id"]:
        raise UploadFinalizeError("finalization_job_mismatch", retryable=False)
    if session["status"] == "completed":
        return
    if session["status"] != "assembling":
        raise UploadFinalizeError("session_not_assembling", retryable=False)


def _ensure_final_file(
    *,
    settings: Settings,
    session: dict[str, Any],
    assembly_path: Path,
    final_path: Path,
) -> str:
    if final_path.is_file():
        final_sha256 = _sha256_file(final_path)
        if final_sha256 != session["expected_file_sha256"]:
            raise UploadFinalizeError("final_original_hash_mismatch", retryable=False)
        return final_sha256

    chunks = _verified_chunks_for_assembly(settings, session)
    assembly_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        digest = hashlib.sha256()
        with assembly_path.open("wb") as output_file:
            for chunk, chunk_path in chunks:
                _copy_verified_chunk(output_file, digest, chunk, chunk_path)
        assembled_sha256 = digest.hexdigest()
        if assembled_sha256 != session["expected_file_sha256"]:
            raise UploadFinalizeError("completed_hash_mismatch", retryable=False)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        assembly_path.replace(final_path)
        return assembled_sha256
    except UploadFinalizeError:
        raise
    except OSError as exc:
        raise UploadFinalizeError("finalization_storage_failure", retryable=True) from exc


def _verified_chunks_for_assembly(
    settings: Settings,
    session: dict[str, Any],
) -> list[tuple[dict[str, Any], Path]]:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        chunks = list_verified_chunks(conn, session["id"])
    expected_count = (session["size_bytes"] + session["chunk_size_bytes"] - 1) // session["chunk_size_bytes"]
    if len(chunks) != expected_count:
        raise UploadFinalizeError("verified_chunks_missing", retryable=False)

    resolved: list[tuple[dict[str, Any], Path]] = []
    for expected_index, chunk in enumerate(chunks):
        expected_start = expected_index * session["chunk_size_bytes"]
        expected_end = min(expected_start + session["chunk_size_bytes"], session["size_bytes"]) - 1
        if (
            chunk["chunk_index"] != expected_index
            or chunk["start_offset"] != expected_start
            or chunk["end_offset"] != expected_end
            or chunk["size_bytes"] != expected_end - expected_start + 1
        ):
            raise UploadFinalizeError("verified_chunk_range_invalid", retryable=False)
        chunk_path = generate_session_chunk_path(settings.media_root, session["id"], expected_index)
        if not chunk_path.is_file():
            raise UploadFinalizeError("verified_chunk_missing", retryable=True)
        resolved.append((chunk, chunk_path))
    return resolved


def _copy_verified_chunk(output_file, digest, chunk: dict[str, Any], chunk_path: Path) -> None:
    chunk_digest = hashlib.sha256()
    copied_size = 0
    try:
        with chunk_path.open("rb") as input_file:
            while data := input_file.read(COPY_BLOCK_SIZE_BYTES):
                output_file.write(data)
                digest.update(data)
                chunk_digest.update(data)
                copied_size += len(data)
    except OSError as exc:
        raise UploadFinalizeError("verified_chunk_missing", retryable=True) from exc
    if copied_size != chunk["size_bytes"] or chunk_digest.hexdigest() != chunk["sha256"]:
        raise UploadFinalizeError("verified_chunk_hash_mismatch", retryable=False)


def _commit_finalization(
    *,
    settings: Settings,
    job: dict[str, Any],
    session: dict[str, Any],
    final_sha256: str,
) -> None:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = get_session(conn, session["id"])
            if current is None:
                raise UploadFinalizeError("session_missing", retryable=False)
            if current["finalization_job_id"] != job["id"]:
                raise UploadFinalizeError("finalization_job_mismatch", retryable=False)
            if current["status"] == "completed":
                conn.execute(
                    "UPDATE jobs SET status = 'done', error_message = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (job["id"],),
                )
                conn.commit()
                return
            if current["status"] != "assembling":
                raise UploadFinalizeError("session_not_assembling", retryable=False)

            asset_row = conn.execute(
                "SELECT * FROM assets WHERE original_path = ?",
                (current["original_relative_path"],),
            ).fetchone()
            if asset_row is None:
                asset = insert_verified_video_asset(
                    conn,
                    filename=current["filename"],
                    original_path=current["original_relative_path"],
                    size_bytes=current["size_bytes"],
                    server_sha256=final_sha256,
                    taken_at=current["taken_at"],
                    latitude=current["latitude"],
                    longitude=current["longitude"],
                    exif_json=exif_json_from_text(current["exif_json"]),
                    is_log=bool(current["is_log"]),
                )
            else:
                asset = dict(asset_row)
                if asset["server_sha256"] != final_sha256:
                    raise UploadFinalizeError("final_original_hash_mismatch", retryable=False)

            phase2b_enabled = has_valid_phase2b_schema(conn)
            preview_job_type = (
                "preview"
                if phase2b_enabled
                else ("lut_preview" if current["is_log"] else "preview")
            )
            preview_generation = 1 if phase2b_enabled else None
            if phase2b_enabled:
                conn.execute(
                    """
                    UPDATE assets
                    SET preview_generation = 1,
                        formal_preview_id = NULL,
                        log_detection_status = 'not_evaluated',
                        source_profile = NULL,
                        detector_rule_version = NULL,
                        detector_manifest_sha256 = NULL,
                        detector_evidence_sha256 = NULL,
                        preview_status = 'preview_generating',
                        review_status = 'not_reviewed',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (asset["id"],),
                )
                conn.execute(
                    """
                    UPDATE upload_sessions
                    SET status = 'completed', asset_id = ?, retryable = 0,
                        failure_code = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (asset["id"], current["id"]),
                )
            preview_job, _created = insert_or_return_job(
                conn,
                job_type=preview_job_type,
                asset_id=asset["id"],
                payload_json=json.dumps(
                    {
                        "asset_id": asset["id"],
                        "original_path": current["original_relative_path"],
                        "type": "video",
                        "is_log": bool(current["is_log"]),
                        **(
                            {
                                "preview_generation": 1,
                                "detection_required": True,
                            }
                            if phase2b_enabled
                            else {}
                        ),
                    },
                    separators=(",", ":"),
                ),
                dedup_key=f"initial-preview:{asset['id']}",
                preview_generation=preview_generation,
            )
            if not phase2b_enabled:
                conn.execute(
                    """
                    UPDATE upload_sessions
                    SET status = 'completed', asset_id = ?, retryable = 0,
                        failure_code = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (asset["id"], current["id"]),
                )
            conn.execute(
                """
                UPDATE jobs
                SET status = 'done', error_message = NULL, claimed_at = NULL,
                    lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job["id"],),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _mark_finalization_failed(
    *,
    settings: Settings,
    job_id: int,
    session_id: str | None,
    code: str,
    retryable: bool,
) -> None:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        with conn:
            if session_id is not None:
                conn.execute(
                    """
                    UPDATE upload_sessions
                    SET status = 'failed', failure_code = ?, retryable = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status != 'completed'
                    """,
                    (code, 1 if retryable else 0, session_id),
                )
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed', error_message = ?, claimed_at = NULL,
                    lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status != 'done'
                """,
                (code[:200], job_id),
            )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        while data := input_file.read(COPY_BLOCK_SIZE_BYTES):
            digest.update(data)
    return digest.hexdigest()


def _cleanup_assembly(path: Path | None) -> None:
    if path is None:
        return
    path.unlink(missing_ok=True)
