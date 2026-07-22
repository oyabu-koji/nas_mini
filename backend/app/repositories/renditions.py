import re
import sqlite3
from typing import Any
from uuid import uuid4

from app.services.preset_manifest import PresetSnapshot


RENDITION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
PRESET_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NONTERMINAL_STATES = frozenset({"queued", "validating", "rendering", "finalizing"})
TERMINAL_STATES = frozenset({"ready", "failed", "superseded"})
ALLOWED_TRANSITIONS = {
    "queued": {"validating", "failed"},
    "validating": {"rendering", "failed"},
    "rendering": {"finalizing", "failed"},
    "finalizing": {"ready", "superseded", "failed"},
}


class RenditionRepositoryError(ValueError):
    pass


def generate_rendition_id() -> str:
    return uuid4().hex


def get_rendition(conn: sqlite3.Connection, rendition_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM renditions WHERE id = ?", (rendition_id,)).fetchone()
    return dict(row) if row is not None else None


def get_rendition_for_asset(
    conn: sqlite3.Connection, *, asset_id: int, rendition_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM renditions WHERE asset_id = ? AND id = ?",
        (asset_id, rendition_id),
    ).fetchone()
    return dict(row) if row is not None else None


def get_rendition_by_client_request(
    conn: sqlite3.Connection, client_request_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM renditions WHERE client_request_id = ?",
        (client_request_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def get_rendition_by_job(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM renditions WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row is not None else None


def increment_selection_generation(conn: sqlite3.Connection, *, asset_id: int) -> int:
    cursor = conn.execute(
        """
        UPDATE assets
        SET rendition_selection_generation = rendition_selection_generation + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (asset_id,),
    )
    if cursor.rowcount != 1:
        raise RenditionRepositoryError("asset not found")
    row = conn.execute(
        "SELECT rendition_selection_generation FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()
    return int(row["rendition_selection_generation"])


def insert_rendition(
    conn: sqlite3.Connection,
    *,
    rendition_id: str,
    asset_id: int,
    client_request_id: str,
    job_id: int,
    selection_generation: int,
    snapshot: PresetSnapshot,
    base_result_id: str | None = None,
    base_derived_file_id: int | None = None,
    base_result_sha256: str | None = None,
) -> dict[str, Any]:
    _validate_id(rendition_id, "rendition ID")
    _validate_id(client_request_id, "client request ID")
    if PRESET_ID_PATTERN.fullmatch(snapshot.requested_preset_id) is None:
        raise RenditionRepositoryError("requested preset ID is invalid")
    conn.execute(
        """
        INSERT INTO renditions (
            id, asset_id, client_request_id, job_id, selection_generation,
            base_result_id, base_derived_file_id, base_result_sha256,
            requested_preset_id, registry_classification, state,
            applied_preset_id, manifest_canonical_bytes, manifest_sha256,
            expected_lut_sha256, preset_version, source_root_kind,
            source_relative_lut_path, preset_display_name, preset_kind,
            source_reference, terms_reference, target_color_space,
            file_format, grid_size
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rendition_id,
            asset_id,
            client_request_id,
            job_id,
            selection_generation,
            base_result_id,
            base_derived_file_id,
            base_result_sha256,
            snapshot.requested_preset_id,
            snapshot.registry_classification,
            None,
            snapshot.manifest_canonical_bytes,
            snapshot.manifest_sha256,
            snapshot.expected_lut_sha256,
            snapshot.version,
            snapshot.source_root_kind,
            snapshot.source_relative_lut_path,
            snapshot.display_name,
            snapshot.preset_kind,
            snapshot.source_reference,
            snapshot.terms_reference,
            snapshot.target_color_space,
            snapshot.file_format,
            snapshot.grid_size,
        ),
    )
    inserted = get_rendition(conn, rendition_id)
    if inserted is None:
        raise RuntimeError("inserted rendition could not be loaded")
    return inserted


def transition_rendition(
    conn: sqlite3.Connection, *, rendition_id: str, new_state: str
) -> dict[str, Any]:
    current = get_rendition(conn, rendition_id)
    if current is None:
        raise RenditionRepositoryError("rendition not found")
    if new_state not in ALLOWED_TRANSITIONS.get(current["state"], set()) or new_state in TERMINAL_STATES:
        raise RenditionRepositoryError("rendition state transition is invalid")
    conn.execute(
        "UPDATE renditions SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_state, rendition_id),
    )
    return get_rendition(conn, rendition_id) or {}


def restart_rendition_validation_in_transaction(
    conn: sqlite3.Connection, *, rendition_id: str
) -> None:
    cursor = conn.execute(
        """
        UPDATE renditions
        SET state = 'validating', updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND state IN ('queued', 'validating', 'rendering', 'finalizing')
        """,
        (rendition_id,),
    )
    if cursor.rowcount != 1:
        raise RenditionRepositoryError("rendition cannot restart validation")


def fail_rendition_in_transaction(
    conn: sqlite3.Connection, *, rendition_id: str, error_code: str
) -> None:
    cursor = conn.execute(
        """
        UPDATE renditions
        SET state = 'failed', color_transform_status = 'failed', error_code = ?,
            result_id = NULL, terminal_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND state IN ('queued', 'validating', 'rendering', 'finalizing')
        """,
        (error_code[:100], rendition_id),
    )
    if cursor.rowcount != 1:
        raise RenditionRepositoryError("rendition cannot be failed")


def complete_rendition_in_transaction(
    conn: sqlite3.Connection,
    *,
    rendition_id: str,
    state: str,
    result_id: str,
    applied_preset_id: str,
    color_transform_status: str,
    error_code: str | None,
) -> None:
    if state not in {"ready", "superseded"}:
        raise RenditionRepositoryError("terminal rendition state is invalid")
    cursor = conn.execute(
        """
        UPDATE renditions
        SET state = ?, result_id = ?, applied_preset_id = ?,
            color_transform_status = ?, error_code = ?,
            terminal_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND state = 'finalizing'
        """,
        (
            state,
            result_id,
            applied_preset_id,
            color_transform_status,
            error_code,
            rendition_id,
        ),
    )
    if cursor.rowcount != 1:
        raise RenditionRepositoryError("rendition cannot be completed")


def serialize_rendition(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rendition_id": row["id"],
        "asset_id": row["asset_id"],
        "client_rendition_request_id": row["client_request_id"],
        "selection_generation": row["selection_generation"],
        "requested_preset_id": row["requested_preset_id"],
        "applied_preset_id": row["applied_preset_id"],
        "state": row["state"],
        "color_transform_status": row["color_transform_status"],
        "error_code": row["error_code"],
        "result_id": row["result_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _validate_id(value: str, label: str) -> None:
    if RENDITION_ID_PATTERN.fullmatch(value) is None:
        raise RenditionRepositoryError(f"{label} is invalid")
