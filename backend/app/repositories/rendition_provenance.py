import sqlite3
from typing import Any


def get_rendition_provenance_by_result(
    conn: sqlite3.Connection, *, result_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM rendition_provenance WHERE result_id = ?", (result_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def insert_rendition_provenance(
    conn: sqlite3.Connection,
    *,
    rendition: dict[str, Any],
    result_id: str,
    derived_file_id: int,
    applied_preset_id: str,
    transform_kind: str,
    color_transform_status: str,
    color_transform_error_code: str | None,
) -> dict[str, Any]:
    conn.execute(
        """
        INSERT INTO rendition_provenance (
            rendition_id, asset_id, result_id, derived_file_id,
            requested_preset_id, applied_preset_id, preset_version,
            manifest_sha256, lut_sha256, transform_kind,
            color_transform_status, color_transform_error_code,
            preset_kind, source_reference, terms_reference, target_color_space
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rendition["id"],
            rendition["asset_id"],
            result_id,
            derived_file_id,
            rendition["requested_preset_id"],
            applied_preset_id,
            rendition["preset_version"],
            rendition["manifest_sha256"],
            rendition["expected_lut_sha256"] if transform_kind == "lut" else None,
            transform_kind,
            color_transform_status,
            color_transform_error_code,
            rendition["preset_kind"],
            rendition["source_reference"],
            rendition["terms_reference"],
            rendition["target_color_space"],
        ),
    )
    inserted = get_rendition_provenance_by_result(conn, result_id=result_id)
    if inserted is None:
        raise RuntimeError("rendition provenance could not be loaded")
    return inserted
