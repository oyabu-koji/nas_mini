from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from app.core.settings import (
    MAX_UPLOAD_CHUNKS,
    MAX_UPLOAD_CHUNK_SIZE_BYTES,
    MAX_UPLOAD_SESSION_SIZE_BYTES,
)
from app.services.formal_preview_authority import (
    has_allowed_formal_transform_claim,
)


CandidateReason = Literal[
    "schema_unavailable",
    "asset_not_session_video",
    "session_not_completed",
    "upload_limit_exceeded",
    "chunk_limit_exceeded",
    "chunk_set_incomplete",
    "file_identity_mismatch",
    "formal_preview_not_ready",
    "formal_preview_provenance_invalid",
    "preview_not_confirmed",
]
CANDIDATE_REASON_ORDER: tuple[CandidateReason, ...] = (
    "schema_unavailable",
    "asset_not_session_video",
    "session_not_completed",
    "upload_limit_exceeded",
    "chunk_limit_exceeded",
    "chunk_set_incomplete",
    "file_identity_mismatch",
    "formal_preview_not_ready",
    "formal_preview_provenance_invalid",
    "preview_not_confirmed",
)

NOT_CANDIDATE = "not_candidate"
SAFE_TO_DELETE_CANDIDATE = "safe_to_delete_candidate"
CANDIDATE_STATUSES = frozenset({NOT_CANDIDATE, SAFE_TO_DELETE_CANDIDATE})


@dataclass(frozen=True)
class CandidateEvaluation:
    eligible: bool
    reason: CandidateReason | None


class CandidateProjectionError(RuntimeError):
    pass


def evaluate_safe_delete_candidate(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
) -> CandidateEvaluation:
    """Evaluate the stored Phase 2 relation without I/O or transaction ownership."""
    try:
        schema = conn.execute(
            """
            SELECT
                EXISTS(
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'assets'
                      AND sql LIKE '%ck_assets_delete_candidate_status%'
                ) AS has_candidate_constraint,
                (
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table'
                      AND name IN (
                          'upload_sessions', 'upload_chunks',
                          'processed_results', 'derived_files',
                          'formal_preview_attempts', 'preview_provenance',
                          'phase2c_schema_metadata'
                      )
                ) AS required_table_count
            """
        ).fetchone()
        if (
            schema is None
            or not schema["has_candidate_constraint"]
            or schema["required_table_count"] != 7
        ):
            return _ineligible("schema_unavailable")

        authority = conn.execute(
            """
            SELECT
                assets.id AS asset_id,
                assets.type AS asset_type,
                assets.size_bytes AS asset_size_bytes,
                assets.server_sha256 AS asset_server_sha256,
                assets.verification_status,
                assets.preview_status,
                assets.review_status,
                assets.preview_generation,
                assets.formal_preview_id,
                assets.log_detection_status,
                assets.source_profile,
                assets.detector_rule_version,
                assets.detector_manifest_sha256,
                assets.detector_evidence_sha256,
                COUNT(upload_sessions.id) AS session_count,
                MAX(upload_sessions.id) AS session_id,
                MAX(upload_sessions.type) AS session_type,
                MAX(upload_sessions.status) AS session_status,
                MAX(upload_sessions.size_bytes) AS session_size_bytes,
                MAX(upload_sessions.expected_file_sha256) AS expected_file_sha256,
                MAX(upload_sessions.chunk_size_bytes) AS chunk_size_bytes
            FROM assets
            LEFT JOIN upload_sessions ON upload_sessions.asset_id = assets.id
            WHERE assets.id = ?
            GROUP BY assets.id
            """,
            (asset_id,),
        ).fetchone()
        if (
            authority is None
            or authority["asset_type"] != "video"
            or authority["session_count"] != 1
            or authority["session_type"] != "video"
        ):
            return _ineligible("asset_not_session_video")
        if authority["session_status"] != "completed":
            return _ineligible("session_not_completed")

        size_bytes = authority["session_size_bytes"]
        chunk_size_bytes = authority["chunk_size_bytes"]
        if (
            not isinstance(size_bytes, int)
            or not 1 <= size_bytes <= MAX_UPLOAD_SESSION_SIZE_BYTES
            or not isinstance(authority["asset_size_bytes"], int)
            or not 1
            <= authority["asset_size_bytes"]
            <= MAX_UPLOAD_SESSION_SIZE_BYTES
        ):
            return _ineligible("upload_limit_exceeded")
        if (
            not isinstance(chunk_size_bytes, int)
            or not 1 <= chunk_size_bytes <= MAX_UPLOAD_CHUNK_SIZE_BYTES
        ):
            return _ineligible("chunk_limit_exceeded")
        total_chunks = (size_bytes + chunk_size_bytes - 1) // chunk_size_bytes
        if not 1 <= total_chunks <= MAX_UPLOAD_CHUNKS:
            return _ineligible("chunk_limit_exceeded")

        chunks = conn.execute(
            """
            SELECT
                COUNT(*) AS row_count,
                SUM(CASE WHEN status = 'verified' THEN 1 ELSE 0 END) AS verified_count,
                COUNT(DISTINCT chunk_index) AS distinct_index_count,
                MIN(chunk_index) AS min_index,
                MAX(chunk_index) AS max_index,
                SUM(
                    CASE
                        WHEN typeof(chunk_index) <> 'integer'
                          OR chunk_index < 0
                          OR chunk_index >= ?
                        THEN 1 ELSE 0
                    END
                ) AS invalid_index_count,
                SUM(
                    CASE WHEN start_offset <> chunk_index * ? THEN 1 ELSE 0 END
                ) AS invalid_start_count,
                SUM(
                    CASE
                        WHEN end_offset <> min(? - 1, ((chunk_index + 1) * ?) - 1)
                        THEN 1 ELSE 0
                    END
                ) AS invalid_end_count,
                SUM(
                    CASE
                        WHEN size_bytes <> (
                            min(? - 1, ((chunk_index + 1) * ?) - 1)
                            - (chunk_index * ?) + 1
                        )
                        THEN 1 ELSE 0
                    END
                ) AS invalid_size_count,
                COALESCE(SUM(size_bytes), 0) AS sum_size_bytes
            FROM upload_chunks INDEXED BY idx_upload_chunks_session
            WHERE session_id = ?
            """,
            (
                total_chunks,
                chunk_size_bytes,
                size_bytes,
                chunk_size_bytes,
                size_bytes,
                chunk_size_bytes,
                chunk_size_bytes,
                authority["session_id"],
            ),
        ).fetchone()
        if not _complete_chunk_set(chunks, total_chunks, size_bytes):
            return _ineligible("chunk_set_incomplete")

        if (
            authority["verification_status"] != "file_verified"
            or authority["asset_size_bytes"] != size_bytes
            or not _is_sha256(authority["expected_file_sha256"])
            or not _is_sha256(authority["asset_server_sha256"])
            or authority["expected_file_sha256"]
            != authority["asset_server_sha256"]
        ):
            return _ineligible("file_identity_mismatch")
        if (
            authority["preview_status"] != "preview_ready"
            or not isinstance(authority["preview_generation"], int)
            or authority["preview_generation"] < 1
            or not isinstance(authority["formal_preview_id"], str)
        ):
            return _ineligible("formal_preview_not_ready")

        formal = conn.execute(
            """
            SELECT
                processed_results.id AS result_id,
                processed_results.asset_id AS result_asset_id,
                processed_results.derived_file_id AS result_derived_file_id,
                processed_results.status AS result_status,
                processed_results.mime_type AS result_mime_type,
                processed_results.size_bytes AS result_size_bytes,
                processed_results.sha256 AS result_sha256,
                processed_results.preview_generation AS result_generation,
                processed_results.superseded_at,
                derived_files.id AS derived_file_id,
                derived_files.asset_id AS derived_asset_id,
                derived_files.kind AS derived_kind,
                derived_files.mime_type AS derived_mime_type,
                derived_files.size_bytes AS derived_size_bytes,
                preview_provenance.id AS provenance_id,
                preview_provenance.attempt_id,
                preview_provenance.asset_id AS provenance_asset_id,
                preview_provenance.preview_generation AS provenance_generation,
                preview_provenance.result_id AS provenance_result_id,
                preview_provenance.derived_file_id AS provenance_derived_file_id,
                preview_provenance.detection_status,
                preview_provenance.source_profile AS provenance_source_profile,
                preview_provenance.detector_rule_version AS provenance_rule_version,
                preview_provenance.detector_manifest_sha256 AS provenance_manifest_identity,
                preview_provenance.detector_evidence_sha256 AS provenance_evidence_identity,
                preview_provenance.requested_preset_id,
                preview_provenance.applied_preset_id,
                preview_provenance.preset_version,
                preview_provenance.manifest_sha256,
                preview_provenance.lut_sha256,
                preview_provenance.transform_kind,
                preview_provenance.color_transform_status,
                preview_provenance.color_transform_error_code,
                formal_preview_attempts.id AS formal_attempt_id,
                formal_preview_attempts.asset_id AS attempt_asset_id,
                formal_preview_attempts.preview_generation AS attempt_generation,
                formal_preview_attempts.state AS attempt_state,
                formal_preview_attempts.result_id AS attempt_result_id,
                formal_preview_attempts.detection_status AS attempt_detection_status,
                formal_preview_attempts.source_profile AS attempt_source_profile,
                formal_preview_attempts.detector_rule_version AS attempt_rule_version,
                formal_preview_attempts.detector_manifest_sha256 AS attempt_manifest_identity,
                formal_preview_attempts.detector_evidence_sha256 AS attempt_evidence_identity,
                formal_preview_attempts.requested_preset_id AS attempt_requested_preset_id,
                formal_preview_attempts.applied_preset_id AS attempt_applied_preset_id,
                formal_preview_attempts.manifest_sha256 AS attempt_manifest_sha256,
                formal_preview_attempts.expected_lut_sha256 AS attempt_lut_sha256,
                formal_preview_attempts.transform_kind AS attempt_transform_kind,
                formal_preview_attempts.color_transform_status AS attempt_transform_status,
                formal_preview_attempts.color_transform_error_code AS attempt_transform_error,
                (
                    SELECT COUNT(*) FROM preview_provenance AS candidate_provenance
                    WHERE candidate_provenance.result_id = assets.formal_preview_id
                ) AS provenance_count,
                (
                    SELECT COUNT(*) FROM formal_preview_attempts AS candidate_attempt
                    WHERE candidate_attempt.asset_id = assets.id
                      AND candidate_attempt.preview_generation = assets.preview_generation
                ) AS attempt_count,
                (
                    SELECT COUNT(*) FROM rendition_provenance
                    WHERE rendition_provenance.result_id = assets.formal_preview_id
                       OR rendition_provenance.derived_file_id =
                          processed_results.derived_file_id
                ) AS managed_provenance_count
            FROM assets
            LEFT JOIN processed_results
              ON processed_results.id = assets.formal_preview_id
            LEFT JOIN derived_files
              ON derived_files.id = processed_results.derived_file_id
            LEFT JOIN preview_provenance
              ON preview_provenance.result_id = processed_results.id
            LEFT JOIN formal_preview_attempts
              ON formal_preview_attempts.id = preview_provenance.attempt_id
            WHERE assets.id = ?
            """,
            (asset_id,),
        ).fetchone()
    except sqlite3.DatabaseError:
        return _ineligible("schema_unavailable")

    if formal is None or not _valid_formal_relation(
        formal,
        authority=authority,
    ):
        return _ineligible("formal_preview_provenance_invalid")
    if authority["review_status"] != "preview_confirmed":
        return _ineligible("preview_not_confirmed")
    return CandidateEvaluation(eligible=True, reason=None)


def project_candidate_status(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    evaluation: CandidateEvaluation,
    allow_promotion: bool,
) -> str:
    row = conn.execute(
        "SELECT delete_candidate_status FROM assets WHERE id = ?",
        (asset_id,),
    ).fetchone()
    if row is None:
        raise CandidateProjectionError("candidate_asset_not_found")
    current = row["delete_candidate_status"]
    if current not in CANDIDATE_STATUSES:
        raise CandidateProjectionError("candidate_status_invalid")

    target = current
    if not evaluation.eligible:
        target = NOT_CANDIDATE
    elif allow_promotion:
        target = SAFE_TO_DELETE_CANDIDATE
    if target != current:
        cursor = conn.execute(
            """
            UPDATE assets
            SET delete_candidate_status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND delete_candidate_status = ?
            """,
            (target, asset_id, current),
        )
        if cursor.rowcount != 1:
            raise CandidateProjectionError("candidate_status_update_failed")
    return target


def _complete_chunk_set(row, total_chunks: int, size_bytes: int) -> bool:
    return bool(
        row is not None
        and row["row_count"] == total_chunks
        and row["verified_count"] == total_chunks
        and row["distinct_index_count"] == total_chunks
        and row["min_index"] == 0
        and row["max_index"] == total_chunks - 1
        and row["invalid_index_count"] == 0
        and row["invalid_start_count"] == 0
        and row["invalid_end_count"] == 0
        and row["invalid_size_count"] == 0
        and row["sum_size_bytes"] == size_bytes
    )


def _valid_formal_relation(row, *, authority) -> bool:
    generation = authority["preview_generation"]
    asset_id = authority["asset_id"]
    result_id = authority["formal_preview_id"]
    identity_pairs = (
        ("detection_status", "log_detection_status"),
        ("provenance_source_profile", "source_profile"),
        ("provenance_rule_version", "detector_rule_version"),
        ("provenance_manifest_identity", "detector_manifest_sha256"),
        ("provenance_evidence_identity", "detector_evidence_sha256"),
    )
    attempt_pairs = (
        ("attempt_detection_status", "detection_status"),
        ("attempt_source_profile", "provenance_source_profile"),
        ("attempt_rule_version", "provenance_rule_version"),
        ("attempt_manifest_identity", "provenance_manifest_identity"),
        ("attempt_evidence_identity", "provenance_evidence_identity"),
        ("attempt_requested_preset_id", "requested_preset_id"),
        ("attempt_applied_preset_id", "applied_preset_id"),
        ("attempt_manifest_sha256", "manifest_sha256"),
        ("attempt_lut_sha256", "lut_sha256"),
        ("attempt_transform_kind", "transform_kind"),
        ("attempt_transform_status", "color_transform_status"),
        ("attempt_transform_error", "color_transform_error_code"),
    )
    common = (
        row["result_id"] == result_id
        and row["result_asset_id"] == asset_id
        and row["result_status"] == "ready"
        and row["result_generation"] == generation
        and row["result_derived_file_id"] == row["derived_file_id"]
        and row["result_mime_type"] == row["derived_mime_type"]
        and isinstance(row["result_mime_type"], str)
        and row["result_mime_type"].startswith("video/")
        and row["result_size_bytes"] == row["derived_size_bytes"]
        and isinstance(row["result_size_bytes"], int)
        and row["result_size_bytes"] > 0
        and _is_sha256(row["result_sha256"])
        and row["superseded_at"] is None
        and row["derived_asset_id"] == asset_id
        and row["derived_kind"] == "preview"
        and row["provenance_count"] == 1
        and row["attempt_count"] == 1
        and row["managed_provenance_count"] == 0
        and row["provenance_id"] is not None
        and row["provenance_asset_id"] == asset_id
        and row["provenance_generation"] == generation
        and row["provenance_result_id"] == result_id
        and row["provenance_derived_file_id"] == row["derived_file_id"]
        and row["formal_attempt_id"] == row["attempt_id"]
        and row["attempt_asset_id"] == asset_id
        and row["attempt_generation"] == generation
        and row["attempt_state"] == "ready"
        and row["attempt_result_id"] == result_id
        and all(row[left] == authority[right] for left, right in identity_pairs)
        and all(row[left] == row[right] for left, right in attempt_pairs)
    )
    return bool(common and has_allowed_formal_transform_claim(dict(row)))


def _is_sha256(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _ineligible(reason: CandidateReason) -> CandidateEvaluation:
    return CandidateEvaluation(eligible=False, reason=reason)
