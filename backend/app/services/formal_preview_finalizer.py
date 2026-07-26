from __future__ import annotations

import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.derived_files import insert_derived_file
from app.repositories.formal_previews import (
    get_formal_preview_attempt_by_job,
    insert_preview_provenance,
    transition_formal_preview_attempt,
)
from app.repositories.processed_results import insert_ready_processed_result
from app.services.processed_result_authority import classify_active_processed_result
from app.services.processed_result_integrity import hash_file_sha256
from app.services.storage import (
    StorageError,
    cleanup_formal_preview_candidate,
    cleanup_uncommitted_formal_preview_output,
    promote_formal_preview_candidate,
    resolve_media_path,
)


FaultInjector = Callable[[str], None]


class FormalPreviewFinalizationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VerifiedOriginal:
    asset_id: int
    relative_path: str
    size_bytes: int
    sha256: str
    path: Path


@dataclass(frozen=True)
class CandidateIdentity:
    size_bytes: int
    sha256: str


def resolve_verified_original(
    *, settings: Settings, conn: sqlite3.Connection, asset_id: int
) -> VerifiedOriginal:
    row = conn.execute(
        """
        SELECT
            assets.id AS asset_id,
            assets.original_path,
            assets.size_bytes,
            assets.server_sha256,
            assets.type,
            assets.verification_status,
            upload_sessions.original_relative_path AS session_original_path,
            upload_sessions.size_bytes AS session_size_bytes,
            upload_sessions.expected_file_sha256 AS session_sha256,
            upload_sessions.status AS session_status,
            upload_sessions.type AS session_type
        FROM assets
        JOIN upload_sessions ON upload_sessions.asset_id = assets.id
        WHERE assets.id = ?
        """,
        (asset_id,),
    ).fetchone()
    if (
        row is None
        or row["type"] != "video"
        or row["verification_status"] != "file_verified"
        or row["session_status"] != "completed"
        or row["session_type"] != "video"
        or not isinstance(row["original_path"], str)
        or not row["original_path"].startswith("originals/sessions/")
        or row["original_path"] != row["session_original_path"]
        or not isinstance(row["size_bytes"], int)
        or row["size_bytes"] <= 0
        or row["size_bytes"] != row["session_size_bytes"]
        or not _is_sha256(row["server_sha256"])
        or row["server_sha256"] != row["session_sha256"]
    ):
        raise FormalPreviewFinalizationError("formal_preview_source_invalid")
    try:
        path = resolve_media_path(settings.media_root, row["original_path"])
        metadata = path.lstat()
        actual_sha256 = hash_file_sha256(path)
    except (OSError, StorageError) as exc:
        raise FormalPreviewFinalizationError("formal_preview_source_invalid") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_size != row["size_bytes"]
        or actual_sha256 != row["server_sha256"]
    ):
        raise FormalPreviewFinalizationError("formal_preview_source_invalid")
    return VerifiedOriginal(
        asset_id=asset_id,
        relative_path=row["original_path"],
        size_bytes=row["size_bytes"],
        sha256=row["server_sha256"],
        path=path,
    )


def inspect_formal_preview_candidate(candidate_path: Path) -> CandidateIdentity:
    try:
        metadata = candidate_path.lstat()
        sha256 = hash_file_sha256(candidate_path)
    except OSError as exc:
        raise FormalPreviewFinalizationError("formal_preview_storage_failed") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or candidate_path.is_symlink()
        or metadata.st_size <= 0
    ):
        raise FormalPreviewFinalizationError("formal_preview_storage_failed")
    return CandidateIdentity(size_bytes=metadata.st_size, sha256=sha256)


def finalize_formal_preview_output(
    *,
    settings: Settings,
    job_id: int,
    attempt_id: str,
    candidate_path: Path,
    candidate_identity: CandidateIdentity,
    fault_injector: FaultInjector | None = None,
) -> bool:
    final_path: Path | None = None
    published = False
    try:
        inspected = inspect_formal_preview_candidate(candidate_path)
        if inspected != candidate_identity:
            raise FormalPreviewFinalizationError("formal_preview_storage_failed")
        relative_path, final_path = promote_formal_preview_candidate(
            settings.media_root,
            candidate_path=candidate_path,
            attempt_id=attempt_id,
        )
        final_identity = inspect_formal_preview_candidate(final_path)
        if final_identity != candidate_identity:
            raise FormalPreviewFinalizationError("formal_preview_storage_failed")

        with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                attempt = get_formal_preview_attempt_by_job(conn, job_id=job_id)
                job = conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                asset = (
                    conn.execute(
                        "SELECT * FROM assets WHERE id = ?", (attempt["asset_id"],)
                    ).fetchone()
                    if attempt is not None
                    else None
                )
                if (
                    attempt is None
                    or attempt["id"] != attempt_id
                    or attempt["state"] != "finalizing"
                    or job is None
                    or job["asset_id"] != attempt["asset_id"]
                    or job["preview_generation"] != attempt["preview_generation"]
                    or asset is None
                ):
                    raise FormalPreviewFinalizationError(
                        "formal_preview_relation_invalid"
                    )
                if asset["preview_generation"] != attempt["preview_generation"]:
                    transition_formal_preview_attempt(
                        conn,
                        attempt_id=attempt_id,
                        new_state="superseded",
                    )
                    _settle_job_failed(
                        conn,
                        job_id=job_id,
                        error_code="preview_generation_superseded",
                    )
                    conn.commit()
                    return False
                _validate_attempt_evidence(attempt)
                resolve_verified_original(
                    settings=settings, conn=conn, asset_id=int(attempt["asset_id"])
                )
                authority = classify_active_processed_result(
                    conn, asset_id=int(attempt["asset_id"])
                )
                if asset["formal_preview_id"] is not None or authority.kind not in {
                    "none",
                    "current_managed",
                }:
                    raise FormalPreviewFinalizationError(
                        "formal_preview_relation_invalid"
                    )

                derived = insert_derived_file(
                    conn,
                    asset_id=int(attempt["asset_id"]),
                    kind="preview",
                    path=relative_path,
                    mime_type="video/mp4",
                    size_bytes=candidate_identity.size_bytes,
                )
                _inject(fault_injector, "after_derived_file")
                result, _created = insert_ready_processed_result(
                    conn,
                    asset_id=int(attempt["asset_id"]),
                    derived_file_id=int(derived["id"]),
                    mime_type="video/mp4",
                    size_bytes=candidate_identity.size_bytes,
                    sha256=candidate_identity.sha256,
                    preview_generation=int(attempt["preview_generation"]),
                )
                _inject(fault_injector, "after_result")
                attempt = transition_formal_preview_attempt(
                    conn,
                    attempt_id=attempt_id,
                    new_state="ready",
                    result_id=result["id"],
                )
                _inject(fault_injector, "after_attempt")
                insert_preview_provenance(
                    conn,
                    attempt=attempt,
                    result_id=result["id"],
                    derived_file_id=int(derived["id"]),
                )
                _inject(fault_injector, "after_provenance")
                active_result_id = (
                    result["id"]
                    if authority.kind == "none"
                    else asset["active_processed_result_id"]
                )
                cursor = conn.execute(
                    """
                    UPDATE assets
                    SET formal_preview_id = ?,
                        active_processed_result_id = ?,
                        log_detection_status = ?,
                        source_profile = ?,
                        detector_rule_version = ?,
                        detector_manifest_sha256 = ?,
                        detector_evidence_sha256 = ?,
                        preview_status = 'preview_ready',
                        review_status = 'not_reviewed',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND preview_generation = ?
                    """,
                    (
                        result["id"],
                        active_result_id,
                        attempt["detection_status"],
                        attempt["source_profile"],
                        attempt["detector_rule_version"],
                        attempt["detector_manifest_sha256"],
                        attempt["detector_evidence_sha256"],
                        attempt["asset_id"],
                        attempt["preview_generation"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise FormalPreviewFinalizationError(
                        "formal_preview_relation_invalid"
                    )
                _inject(fault_injector, "after_asset")
                _settle_job_done(conn, job_id=job_id)
                _inject(fault_injector, "after_job")
                conn.commit()
                published = True
            except Exception:
                conn.rollback()
                raise
    except FormalPreviewFinalizationError:
        raise
    except (OSError, StorageError) as exc:
        raise FormalPreviewFinalizationError("formal_preview_storage_failed") from exc
    except sqlite3.DatabaseError as exc:
        raise FormalPreviewFinalizationError("formal_preview_database_failed") from exc
    finally:
        if published:
            cleanup_formal_preview_candidate(settings.media_root, attempt_id)
        elif final_path is not None:
            cleanup_uncommitted_formal_preview_output(settings.media_root, attempt_id)
            cleanup_formal_preview_candidate(settings.media_root, attempt_id)
    return True


def _validate_attempt_evidence(attempt: dict) -> None:
    detection_status = attempt.get("detection_status")
    common_valid = (
        detection_status in {"apple_log", "not_log", "unknown"}
        and _is_sha256(attempt.get("detector_manifest_sha256"))
        and _is_sha256(attempt.get("detector_evidence_sha256"))
        and isinstance(attempt.get("detector_rule_version"), str)
        and isinstance(attempt.get("detector_evidence_json"), bytes)
    )
    apple_fallback = (
        detection_status == "apple_log"
        and attempt.get("requested_preset_id") == "generated-apple-log-rec709"
        and attempt.get("registry_classification") in {"absent", "disabled"}
        and attempt.get("applied_preset_id") == "compress-only"
        and attempt.get("transform_kind") == "none"
        and attempt.get("color_transform_status") == "unavailable"
        and attempt.get("color_transform_error_code") == "lut_preset_unavailable"
        and attempt.get("manifest_sha256") is None
        and attempt.get("expected_lut_sha256") is None
    )
    ordinary = (
        detection_status in {"not_log", "unknown"}
        and attempt.get("requested_preset_id") == "compress-only"
        and attempt.get("registry_classification") == "valid"
        and attempt.get("applied_preset_id") == "compress-only"
        and attempt.get("transform_kind") == "none"
        and attempt.get("color_transform_status") == "not_requested"
        and attempt.get("color_transform_error_code") is None
        and attempt.get("manifest_sha256") is None
        and attempt.get("expected_lut_sha256") is None
    )
    if not common_valid or not (apple_fallback or ordinary):
        raise FormalPreviewFinalizationError("formal_preview_relation_invalid")


def _settle_job_done(conn: sqlite3.Connection, *, job_id: int) -> None:
    cursor = conn.execute(
        """
        UPDATE jobs
        SET status = 'done', error_message = NULL, claimed_at = NULL,
            lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (job_id,),
    )
    if cursor.rowcount != 1:
        raise FormalPreviewFinalizationError("formal_preview_relation_invalid")


def _settle_job_failed(
    conn: sqlite3.Connection, *, job_id: int, error_code: str
) -> None:
    cursor = conn.execute(
        """
        UPDATE jobs
        SET status = 'failed', error_message = ?, claimed_at = NULL,
            lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (error_code, job_id),
    )
    if cursor.rowcount != 1:
        raise FormalPreviewFinalizationError("formal_preview_relation_invalid")


def _is_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _inject(injector: FaultInjector | None, step: str) -> None:
    if injector is not None:
        injector(step)
