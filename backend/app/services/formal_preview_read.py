from __future__ import annotations

from typing import Any

from app.core.settings import Settings
from app.schemas.assets import (
    FormalPreviewFailedResponse,
    FormalPreviewFailureCode,
    FormalPreviewGeneratingResponse,
    FormalPreviewReadyResponse,
    ProcessedResultMetadataResponse,
)
from app.services.processed_result_delivery import resolve_formal_preview_result


FAILURE_CODES = frozenset(FormalPreviewFailureCode.__args__)


def build_formal_preview_response(
    *, settings: Settings, conn, asset: dict[str, Any]
):
    generation = asset.get("preview_generation")
    if (
        "formal_preview_id" not in asset
        or not isinstance(generation, int)
        or generation < 1
        or not _is_phase2b_session_video(conn, asset_id=int(asset["id"]))
    ):
        return None
    attempt_row = conn.execute(
        """
        SELECT * FROM formal_preview_attempts
        WHERE asset_id = ? AND preview_generation = ?
        """,
        (asset["id"], generation),
    ).fetchone()
    attempt = dict(attempt_row) if attempt_row is not None else None
    if attempt is not None and attempt["state"] == "failed":
        return _failed_response(attempt=attempt, generation=generation)
    if asset.get("preview_status") == "preview_ready":
        ready = _ready_response(
            settings=settings,
            conn=conn,
            asset=asset,
            attempt=attempt,
            generation=generation,
        )
        if ready is not None:
            return ready
        return _relation_failed_response(attempt=attempt, generation=generation)
    if asset.get("preview_status") == "failed":
        if attempt is not None and attempt.get("failure_code") in FAILURE_CODES:
            return _failed_response(attempt=attempt, generation=generation)
        return _relation_failed_response(attempt=attempt, generation=generation)
    return _generating_response(attempt=attempt, generation=generation)


def _generating_response(
    *, attempt: dict[str, Any] | None, generation: int
) -> FormalPreviewGeneratingResponse:
    values = _detector_values(attempt)
    applied = _value(attempt, "color_transform_status") == "applied"
    return FormalPreviewGeneratingResponse(
        state="generating",
        generation=generation,
        **values,
        requested_preset_id=_value(attempt, "requested_preset_id"),
        applied_preset_id=_value(attempt, "applied_preset_id"),
        applied_preset_display_name=(
            _value(attempt, "preset_display_name") if applied else None
        ),
        preset_version=_value(attempt, "preset_version") if applied else None,
        manifest_sha256=_value(attempt, "manifest_sha256") if applied else None,
        lut_sha256=_value(attempt, "expected_lut_sha256") if applied else None,
        transform_kind=_value(attempt, "transform_kind"),
        color_transform_status=_value(attempt, "color_transform_status"),
        color_transform_error_code=_value(
            attempt, "color_transform_error_code"
        ),
    )


def _failed_response(
    *, attempt: dict[str, Any], generation: int
) -> FormalPreviewFailedResponse:
    transform_kind = (
        attempt.get("transform_kind")
        if attempt.get("requested_preset_id") is not None
        else None
    )
    return FormalPreviewFailedResponse(
        state="failed",
        generation=generation,
        **_detector_values(attempt),
        requested_preset_id=attempt.get("requested_preset_id"),
        transform_kind=transform_kind,
        color_transform_status="failed" if transform_kind is not None else None,
        color_transform_error_code=(
            attempt.get("failure_code") if transform_kind is not None else None
        ),
        failure_code=attempt["failure_code"],
    )


def _relation_failed_response(
    *, attempt: dict[str, Any] | None, generation: int
) -> FormalPreviewFailedResponse:
    return FormalPreviewFailedResponse(
        state="failed",
        generation=generation,
        **_detector_values(attempt),
        requested_preset_id=_value(attempt, "requested_preset_id"),
        failure_code="formal_preview_relation_invalid",
    )


def _ready_response(
    *,
    settings: Settings,
    conn,
    asset: dict[str, Any],
    attempt: dict[str, Any] | None,
    generation: int,
) -> FormalPreviewReadyResponse | None:
    if attempt is None or attempt.get("state") != "ready":
        return None
    delivery = resolve_formal_preview_result(
        settings=settings, conn=conn, asset=asset
    )
    if delivery is None:
        return None
    provenance_row = conn.execute(
        "SELECT * FROM preview_provenance WHERE attempt_id = ?",
        (attempt["id"],),
    ).fetchone()
    if provenance_row is None:
        return None
    provenance = dict(provenance_row)
    result = delivery.result
    try:
        return FormalPreviewReadyResponse(
            state="ready",
            generation=generation,
            detection_status=provenance["detection_status"],
            source_profile=provenance["source_profile"],
            detector_rule_version=provenance["detector_rule_version"],
            detector_manifest_sha256=provenance[
                "detector_manifest_sha256"
            ],
            detector_evidence_sha256=provenance[
                "detector_evidence_sha256"
            ],
            requested_preset_id=provenance["requested_preset_id"],
            applied_preset_id=provenance["applied_preset_id"],
            applied_preset_display_name=(
                provenance["preset_display_name"]
                if provenance["color_transform_status"] == "applied"
                else None
            ),
            preset_version=(
                provenance["preset_version"]
                if provenance["color_transform_status"] == "applied"
                else None
            ),
            manifest_sha256=provenance["manifest_sha256"],
            lut_sha256=provenance["lut_sha256"],
            transform_kind=provenance["transform_kind"],
            color_transform_status=provenance[
                "color_transform_status"
            ],
            color_transform_error_code=provenance[
                "color_transform_error_code"
            ],
            preview_id=provenance["id"],
            result=ProcessedResultMetadataResponse(
                result_id=result["id"],
                mime_type=result["mime_type"],
                size_bytes=result["size_bytes"],
                sha256=result["sha256"],
                created_at=_result_created_at(str(result["created_at"])),
                url=f"/assets/{asset['id']}/results/{result['id']}",
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _detector_values(
    attempt: dict[str, Any] | None,
) -> dict[str, Any]:
    if (
        attempt is None
        or attempt.get("detection_status") not in {
            "apple_log",
            "not_log",
            "unknown",
        }
        or not all(
            isinstance(attempt.get(field), str)
            for field in (
                "detector_rule_version",
                "detector_manifest_sha256",
                "detector_evidence_sha256",
            )
        )
    ):
        return {
            "detection_status": None,
            "source_profile": None,
            "detector_rule_version": None,
            "detector_manifest_sha256": None,
            "detector_evidence_sha256": None,
        }
    return {
        "detection_status": attempt["detection_status"],
        "source_profile": attempt["source_profile"],
        "detector_rule_version": attempt["detector_rule_version"],
        "detector_manifest_sha256": attempt["detector_manifest_sha256"],
        "detector_evidence_sha256": attempt["detector_evidence_sha256"],
    }


def _is_phase2b_session_video(conn, *, asset_id: int) -> bool:
    return (
        conn.execute(
            """
            SELECT 1 FROM upload_sessions
            WHERE asset_id = ? AND type = 'video'
            """,
            (asset_id,),
        ).fetchone()
        is not None
    )


def _value(attempt: dict[str, Any] | None, field: str):
    return attempt.get(field) if attempt is not None else None


def _result_created_at(value: str) -> str:
    if "T" in value:
        return value
    return value.replace(" ", "T", 1) + "Z"
