from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Literal


ActiveResultKind = Literal["none", "legacy_phase2a", "current_managed", "current_formal", "ambiguous"]


@dataclass(frozen=True)
class ActiveResultAuthority:
    kind: ActiveResultKind
    result: dict[str, Any] | None
    derived_file: dict[str, Any] | None
    provenance: dict[str, Any] | None


def classify_active_processed_result(
    conn: sqlite3.Connection, *, asset_id: int
) -> ActiveResultAuthority:
    asset = conn.execute(
        "SELECT * FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()
    if asset is None:
        return _ambiguous()
    active_id = asset["active_processed_result_id"]
    if active_id is None:
        return ActiveResultAuthority("none", None, None, None)

    result = conn.execute(
        "SELECT * FROM processed_results WHERE id = ? AND asset_id = ?",
        (active_id, asset_id),
    ).fetchone()
    if result is None or result["status"] != "ready" or result["derived_file_id"] is None:
        return _ambiguous()
    derived = conn.execute(
        "SELECT * FROM derived_files WHERE id = ? AND asset_id = ?",
        (result["derived_file_id"], asset_id),
    ).fetchone()
    if derived is None:
        return _ambiguous()

    preview_provenance = _optional_relation(
        conn,
        "SELECT * FROM preview_provenance WHERE result_id = ?",
        active_id,
    )
    managed_provenance = _optional_relation(
        conn,
        "SELECT * FROM rendition_provenance WHERE result_id = ?",
        active_id,
    )
    if preview_provenance == "ambiguous" or managed_provenance == "ambiguous":
        return _ambiguous()
    if preview_provenance is not None and managed_provenance is not None:
        return _ambiguous()

    result_dict = dict(result)
    derived_dict = dict(derived)
    if preview_provenance is not None:
        if (
            asset["formal_preview_id"] != active_id
            or result["preview_generation"] != asset["preview_generation"]
            or preview_provenance["asset_id"] != asset_id
            or preview_provenance["preview_generation"] != asset["preview_generation"]
            or derived["kind"] != "preview"
        ):
            return _ambiguous()
        return ActiveResultAuthority(
            "current_formal",
            result_dict,
            derived_dict,
            dict(preview_provenance),
        )

    if managed_provenance is not None:
        rendition = conn.execute(
            "SELECT * FROM renditions WHERE id = ?",
            (managed_provenance["rendition_id"],),
        ).fetchone()
        if (
            rendition is None
            or derived["kind"] != "rendition"
            or result["preview_generation"] is not None
            or managed_provenance["asset_id"] != asset_id
            or managed_provenance["derived_file_id"] != derived["id"]
            or rendition["asset_id"] != asset_id
            or rendition["result_id"] != active_id
            or rendition["state"] != "ready"
            or rendition["selection_generation"] > asset["rendition_selection_generation"]
            or _has_newer_current_candidate(
                conn,
                asset_id=asset_id,
                selection_generation=rendition["selection_generation"],
            )
        ):
            return _ambiguous()
        return ActiveResultAuthority(
            "current_managed",
            result_dict,
            derived_dict,
            dict(managed_provenance),
        )

    if (
        derived["kind"] == "preview"
        and result["preview_generation"] is None
        and not _result_has_any_provenance(conn, active_id)
    ):
        return ActiveResultAuthority(
            "legacy_phase2a",
            result_dict,
            derived_dict,
            None,
        )
    return _ambiguous()


def validate_managed_pointer_transition_source(
    conn: sqlite3.Connection,
    *,
    asset: dict[str, Any],
    rendition: dict[str, Any],
    active: dict[str, Any],
) -> bool:
    if (
        "formal_preview_id" not in asset
        or asset.get("type") != "video"
        or asset.get("verification_status") != "file_verified"
        or rendition.get("asset_id") != asset.get("id")
        or rendition.get("state") != "finalizing"
        or rendition.get("selection_generation")
        != asset.get("rendition_selection_generation")
        or active.get("id") != asset.get("active_processed_result_id")
        or active.get("status") != "ready"
        or active.get("derived_file_id") is None
        or not _completed_video_session(conn, asset_id=int(asset["id"]))
    ):
        return False
    if active["id"] == asset.get("formal_preview_id"):
        return _valid_formal_source(
            conn, asset=asset, result_id=str(active["id"])
        )
    return _valid_managed_transition_source(
        conn,
        asset=asset,
        rendition=rendition,
        result_id=str(active["id"]),
    )


def _valid_formal_source(
    conn: sqlite3.Connection, *, asset: dict[str, Any], result_id: str
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM preview_provenance
        JOIN formal_preview_attempts
          ON formal_preview_attempts.id = preview_provenance.attempt_id
        JOIN processed_results
          ON processed_results.id = preview_provenance.result_id
        JOIN derived_files
          ON derived_files.id = preview_provenance.derived_file_id
        WHERE preview_provenance.result_id = ?
          AND preview_provenance.asset_id = ?
          AND preview_provenance.preview_generation = ?
          AND formal_preview_attempts.state = 'ready'
          AND formal_preview_attempts.result_id = preview_provenance.result_id
          AND processed_results.status = 'ready'
          AND processed_results.preview_generation = ?
          AND derived_files.asset_id = ?
          AND derived_files.kind = 'preview'
        """,
        (
            result_id,
            asset["id"],
            asset["preview_generation"],
            asset["preview_generation"],
            asset["id"],
        ),
    ).fetchone()
    return row is not None


def _valid_managed_transition_source(
    conn: sqlite3.Connection,
    *,
    asset: dict[str, Any],
    rendition: dict[str, Any],
    result_id: str,
) -> bool:
    row = conn.execute(
        """
        SELECT renditions.selection_generation
        FROM rendition_provenance
        JOIN renditions
          ON renditions.id = rendition_provenance.rendition_id
        JOIN processed_results
          ON processed_results.id = rendition_provenance.result_id
        JOIN derived_files
          ON derived_files.id = rendition_provenance.derived_file_id
        WHERE rendition_provenance.result_id = ?
          AND rendition_provenance.asset_id = ?
          AND renditions.asset_id = ?
          AND renditions.state = 'ready'
          AND renditions.result_id = processed_results.id
          AND processed_results.status = 'ready'
          AND processed_results.preview_generation IS NULL
          AND derived_files.asset_id = ?
          AND derived_files.kind = 'rendition'
        """,
        (result_id, asset["id"], asset["id"], asset["id"]),
    ).fetchone()
    if row is None or row["selection_generation"] >= rendition["selection_generation"]:
        return False
    competing = conn.execute(
        """
        SELECT 1
        FROM renditions
        WHERE asset_id = ?
          AND id <> ?
          AND selection_generation > ?
          AND state IN ('queued', 'validating', 'rendering', 'finalizing', 'ready')
        LIMIT 1
        """,
        (
            asset["id"],
            rendition["id"],
            row["selection_generation"],
        ),
    ).fetchone()
    return competing is None


def _completed_video_session(conn: sqlite3.Connection, *, asset_id: int) -> bool:
    return (
        conn.execute(
            """
            SELECT 1 FROM upload_sessions
            WHERE asset_id = ? AND type = 'video' AND status = 'completed'
            """,
            (asset_id,),
        ).fetchone()
        is not None
    )


def _has_newer_current_candidate(
    conn: sqlite3.Connection, *, asset_id: int, selection_generation: int
) -> bool:
    return (
        conn.execute(
            """
            SELECT 1 FROM renditions
            WHERE asset_id = ?
              AND selection_generation > ?
              AND state IN ('queued', 'validating', 'rendering', 'finalizing', 'ready')
            LIMIT 1
            """,
            (asset_id, selection_generation),
        ).fetchone()
        is not None
    )


def _result_has_any_provenance(conn: sqlite3.Connection, result_id: str) -> bool:
    return bool(
        conn.execute(
            """
            SELECT
                EXISTS(SELECT 1 FROM preview_provenance WHERE result_id = ?)
                OR EXISTS(SELECT 1 FROM rendition_provenance WHERE result_id = ?)
            """,
            (result_id, result_id),
        ).fetchone()[0]
    )


def _optional_relation(
    conn: sqlite3.Connection, sql: str, result_id: str
) -> sqlite3.Row | Literal["ambiguous"] | None:
    rows = conn.execute(sql, (result_id,)).fetchall()
    if len(rows) > 1:
        return "ambiguous"
    return rows[0] if rows else None


def _ambiguous() -> ActiveResultAuthority:
    return ActiveResultAuthority("ambiguous", None, None, None)
