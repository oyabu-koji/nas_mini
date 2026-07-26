from __future__ import annotations

import sqlite3
from typing import Any
from uuid import uuid4

from app.services.preset_manifest import PresetSnapshot


ALLOWED_ATTEMPT_TRANSITIONS = {
    "queued": {"probing", "failed", "superseded"},
    "probing": {"resolving", "failed", "superseded"},
    "resolving": {"rendering", "failed", "superseded"},
    "rendering": {"finalizing", "failed", "superseded"},
    "finalizing": {"ready", "failed", "superseded"},
}
TERMINAL_ATTEMPT_STATES = frozenset({"ready", "failed", "superseded"})


class FormalPreviewRepositoryError(ValueError):
    pass


def get_formal_preview_attempt(
    conn: sqlite3.Connection, *, attempt_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM formal_preview_attempts WHERE id = ?", (attempt_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def get_formal_preview_attempt_by_job(
    conn: sqlite3.Connection, *, job_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM formal_preview_attempts WHERE job_id = ?", (job_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def insert_or_get_formal_preview_attempt(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    job_id: int,
    preview_generation: int,
    attempt_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    existing = get_formal_preview_attempt_by_job(conn, job_id=job_id)
    if existing is not None:
        if (
            existing["asset_id"] != asset_id
            or existing["preview_generation"] != preview_generation
        ):
            raise FormalPreviewRepositoryError("formal preview attempt identity conflict")
        return existing, False
    assigned_id = attempt_id or uuid4().hex
    conn.execute(
        """
        INSERT INTO formal_preview_attempts (
            id, asset_id, job_id, preview_generation, state
        ) VALUES (?, ?, ?, ?, 'queued')
        """,
        (assigned_id, asset_id, job_id, preview_generation),
    )
    inserted = get_formal_preview_attempt(conn, attempt_id=assigned_id)
    if inserted is None:
        raise RuntimeError("formal preview attempt could not be loaded")
    return inserted, True


def save_detection_snapshot(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    detection_status: str,
    source_profile: str | None,
    detector_rule_version: str,
    detector_manifest_sha256: str,
    detector_evidence_sha256: str,
    detector_evidence_json: bytes,
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        UPDATE formal_preview_attempts
        SET state = 'resolving',
            detection_status = ?,
            source_profile = ?,
            detector_rule_version = ?,
            detector_manifest_sha256 = ?,
            detector_evidence_sha256 = ?,
            detector_evidence_json = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND state IN ('queued', 'probing')
        """,
        (
            detection_status,
            source_profile,
            detector_rule_version,
            detector_manifest_sha256,
            detector_evidence_sha256,
            detector_evidence_json,
            attempt_id,
        ),
    )
    if cursor.rowcount != 1:
        raise FormalPreviewRepositoryError("detection snapshot cannot be saved")
    return get_formal_preview_attempt(conn, attempt_id=attempt_id) or {}


def save_preset_snapshot(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    snapshot: PresetSnapshot,
    transform_kind: str,
    color_transform_status: str,
    color_transform_error_code: str | None,
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        UPDATE formal_preview_attempts
        SET state = 'rendering',
            requested_preset_id = ?,
            registry_classification = ?,
            applied_preset_id = ?,
            preset_display_name = ?,
            preset_kind = ?,
            preset_version = ?,
            source_reference = ?,
            terms_reference = ?,
            target_color_space = ?,
            manifest_canonical_bytes = ?,
            manifest_sha256 = ?,
            expected_lut_sha256 = ?,
            file_format = ?,
            grid_size = ?,
            source_root_kind = ?,
            source_relative_lut_path = ?,
            transform_kind = ?,
            color_transform_status = ?,
            color_transform_error_code = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND state = 'resolving'
        """,
        (
            snapshot.requested_preset_id,
            snapshot.registry_classification,
            snapshot.applied_preset_id,
            snapshot.display_name,
            snapshot.preset_kind,
            snapshot.version,
            snapshot.source_reference,
            snapshot.terms_reference,
            snapshot.target_color_space,
            snapshot.manifest_canonical_bytes,
            snapshot.manifest_sha256,
            snapshot.expected_lut_sha256,
            snapshot.file_format,
            snapshot.grid_size,
            snapshot.source_root_kind,
            snapshot.source_relative_lut_path,
            transform_kind,
            color_transform_status,
            color_transform_error_code,
            attempt_id,
        ),
    )
    if cursor.rowcount != 1:
        raise FormalPreviewRepositoryError("preset snapshot cannot be saved")
    return get_formal_preview_attempt(conn, attempt_id=attempt_id) or {}


def transition_formal_preview_attempt(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    new_state: str,
    result_id: str | None = None,
    failure_code: str | None = None,
) -> dict[str, Any]:
    current = get_formal_preview_attempt(conn, attempt_id=attempt_id)
    if current is None:
        raise FormalPreviewRepositoryError("formal preview attempt not found")
    if new_state not in ALLOWED_ATTEMPT_TRANSITIONS.get(current["state"], set()):
        raise FormalPreviewRepositoryError("formal preview attempt transition invalid")
    if new_state == "ready":
        if result_id is None or failure_code is not None:
            raise FormalPreviewRepositoryError("ready attempt outcome invalid")
    elif new_state == "failed":
        if result_id is not None or not failure_code:
            raise FormalPreviewRepositoryError("failed attempt outcome invalid")
    elif new_state == "superseded":
        if result_id is not None or failure_code is not None:
            raise FormalPreviewRepositoryError("superseded attempt outcome invalid")
    elif result_id is not None or failure_code is not None:
        raise FormalPreviewRepositoryError("nonterminal attempt outcome invalid")

    cursor = conn.execute(
        """
        UPDATE formal_preview_attempts
        SET state = ?, result_id = ?, failure_code = ?,
            terminal_at = CASE
                WHEN ? IN ('ready', 'failed', 'superseded') THEN CURRENT_TIMESTAMP
                ELSE NULL
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (new_state, result_id, failure_code, new_state, attempt_id),
    )
    if cursor.rowcount != 1:
        raise FormalPreviewRepositoryError("formal preview attempt update failed")
    return get_formal_preview_attempt(conn, attempt_id=attempt_id) or {}


def insert_preview_provenance(
    conn: sqlite3.Connection,
    *,
    attempt: dict[str, Any],
    result_id: str,
    derived_file_id: int,
    provenance_id: str | None = None,
) -> dict[str, Any]:
    assigned_id = provenance_id or uuid4().hex
    conn.execute(
        """
        INSERT INTO preview_provenance (
            id, attempt_id, asset_id, preview_generation, result_id,
            derived_file_id, detection_status, source_profile,
            detector_rule_version, detector_manifest_sha256,
            detector_evidence_sha256, requested_preset_id,
            applied_preset_id, preset_display_name, preset_kind,
            preset_version, manifest_sha256, lut_sha256, transform_kind,
            color_transform_status, color_transform_error_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assigned_id,
            attempt["id"],
            attempt["asset_id"],
            attempt["preview_generation"],
            result_id,
            derived_file_id,
            attempt["detection_status"],
            attempt["source_profile"],
            attempt["detector_rule_version"],
            attempt["detector_manifest_sha256"],
            attempt["detector_evidence_sha256"],
            attempt["requested_preset_id"],
            attempt["applied_preset_id"],
            attempt["preset_display_name"],
            attempt["preset_kind"],
            attempt["preset_version"],
            attempt["manifest_sha256"],
            attempt["expected_lut_sha256"],
            attempt["transform_kind"],
            attempt["color_transform_status"],
            attempt["color_transform_error_code"],
        ),
    )
    row = conn.execute(
        "SELECT * FROM preview_provenance WHERE id = ?", (assigned_id,)
    ).fetchone()
    if row is None:
        raise RuntimeError("formal preview provenance could not be loaded")
    return dict(row)
