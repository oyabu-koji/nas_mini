from dataclasses import dataclass
from typing import Any, Callable

from app.core.settings import Settings
from app.repositories.assets import PREVIEW_STATUS_PREVIEW_READY, VERIFICATION_STATUS_FILE_VERIFIED
from app.repositories.derived_files import get_derived_file
from app.repositories.processed_results import (
    get_active_processed_result,
    get_processed_result,
    is_phase2a_session_video_asset,
)
from app.repositories.rendition_provenance import get_rendition_provenance_by_result
from app.repositories.renditions import get_rendition
from app.services.processed_result_integrity import (
    ProcessedResultIntegrityError,
    VerifiedProcessedResult,
    verify_processed_result,
)
from app.services.processed_result_authority import classify_active_processed_result


@dataclass(frozen=True)
class DeliverableProcessedResult:
    result: dict[str, Any]
    derived_file: dict[str, Any]
    verified_file: VerifiedProcessedResult


FormalPreviewProvenanceValidator = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], bool]


def resolve_deliverable_result(
    *,
    settings: Settings,
    conn,
    asset: dict[str, Any],
    formal_preview_provenance_validator: FormalPreviewProvenanceValidator | None = None,
) -> DeliverableProcessedResult | None:
    """Resolve the current selected result using persisted result authority."""
    active_result = get_active_processed_result(conn, asset_id=int(asset["id"]))
    if active_result is None:
        return None
    if "formal_preview_id" in asset:
        return resolve_deliverable_result_by_id(
            settings=settings,
            conn=conn,
            asset=asset,
            result_id=str(active_result["id"]),
        )
    if not is_phase2a_deliverable_asset(conn=conn, asset=asset):
        return None
    return _verify_delivery(
        settings=settings,
        conn=conn,
        asset=asset,
        result=active_result,
        formal_preview_provenance_validator=formal_preview_provenance_validator,
    )


def resolve_deliverable_result_by_id(
    *,
    settings: Settings,
    conn,
    asset: dict[str, Any],
    result_id: str,
) -> DeliverableProcessedResult | None:
    requested = get_processed_result(
        conn, asset_id=int(asset["id"]), result_id=result_id
    )
    if requested is None or requested.get("status") != "ready":
        return None
    if "formal_preview_id" not in asset:
        active = get_active_processed_result(conn, asset_id=int(asset["id"]))
        if (
            active is None
            or active["id"] != result_id
            or not is_phase2a_deliverable_asset(conn=conn, asset=asset)
        ):
            return None
        return _verify_delivery(
            settings=settings,
            conn=conn,
            asset=asset,
            result=requested,
        )

    if asset.get("formal_preview_id") == result_id:
        return _resolve_formal_delivery(
            settings=settings, conn=conn, asset=asset, result=requested
        )
    authority = classify_active_processed_result(conn, asset_id=int(asset["id"]))
    if (
        asset.get("active_processed_result_id") == result_id
        and authority.kind == "current_managed"
        and authority.result is not None
        and authority.derived_file is not None
    ):
        return _verify_delivery(
            settings=settings,
            conn=conn,
            asset=asset,
            result=authority.result,
            required_derived=authority.derived_file,
            require_managed=True,
        )
    return None


def resolve_formal_preview_result(
    *, settings: Settings, conn, asset: dict[str, Any]
) -> DeliverableProcessedResult | None:
    result_id = asset.get("formal_preview_id")
    if not isinstance(result_id, str):
        return None
    result = get_processed_result(
        conn, asset_id=int(asset["id"]), result_id=result_id
    )
    if result is None:
        return None
    return _resolve_formal_delivery(
        settings=settings, conn=conn, asset=asset, result=result
    )


def _verify_delivery(
    *,
    settings: Settings,
    conn,
    asset: dict[str, Any],
    result: dict[str, Any],
    required_derived: dict[str, Any] | None = None,
    require_managed: bool = False,
    formal_preview_provenance_validator: FormalPreviewProvenanceValidator | None = None,
) -> DeliverableProcessedResult | None:
    derived_file_id = result.get("derived_file_id")
    if not isinstance(derived_file_id, int):
        return None
    derived_file = required_derived or get_derived_file(conn, derived_file_id)
    if derived_file is None or derived_file.get("id") != derived_file_id:
        return None
    if require_managed or derived_file.get("kind") == "rendition":
        if not _valid_managed_provenance(
            conn=conn,
            asset=asset,
            result=result,
            derived_file=derived_file,
        ):
            return None
    if derived_file.get("kind") not in {"preview", "rendition"}:
        return None
    if not _passes_phase2b_forward_gate(
        asset=asset,
        result=result,
        derived_file=derived_file,
        formal_preview_provenance_validator=formal_preview_provenance_validator,
    ):
        return None
    try:
        verified_file = verify_processed_result(
            settings=settings,
            result=result,
            derived_file=derived_file,
        )
    except ProcessedResultIntegrityError:
        return None
    return DeliverableProcessedResult(
        result=result,
        derived_file=derived_file,
        verified_file=verified_file,
    )


def _resolve_formal_delivery(
    *,
    settings: Settings,
    conn,
    asset: dict[str, Any],
    result: dict[str, Any],
) -> DeliverableProcessedResult | None:
    if (
        asset.get("preview_status") != PREVIEW_STATUS_PREVIEW_READY
        or result.get("status") != "ready"
        or result.get("preview_generation") != asset.get("preview_generation")
    ):
        return None
    derived_file_id = result.get("derived_file_id")
    if not isinstance(derived_file_id, int):
        return None
    derived_file = get_derived_file(conn, derived_file_id)
    if derived_file is None:
        return None
    provenance = conn.execute(
        """
        SELECT preview_provenance.*, formal_preview_attempts.state AS attempt_state,
               formal_preview_attempts.result_id AS attempt_result_id,
               formal_preview_attempts.detector_evidence_json
        FROM preview_provenance
        JOIN formal_preview_attempts
          ON formal_preview_attempts.id = preview_provenance.attempt_id
        WHERE preview_provenance.result_id = ?
        """,
        (result["id"],),
    ).fetchone()
    if provenance is None or not _valid_formal_provenance(
        asset=asset,
        result=result,
        derived_file=derived_file,
        provenance=dict(provenance),
    ):
        return None
    return _verify_delivery(
        settings=settings,
        conn=conn,
        asset=asset,
        result=result,
        required_derived=derived_file,
    )


def is_phase2a_deliverable_asset(*, conn, asset: dict[str, Any]) -> bool:
    return (
        asset.get("type") == "video"
        and asset.get("verification_status") == VERIFICATION_STATUS_FILE_VERIFIED
        and asset.get("preview_status") == PREVIEW_STATUS_PREVIEW_READY
        and not bool(asset.get("is_log"))
        and is_phase2a_session_video_asset(conn, asset_id=int(asset["id"]))
    )


def _valid_formal_provenance(
    *,
    asset: dict[str, Any],
    result: dict[str, Any],
    derived_file: dict[str, Any],
    provenance: dict[str, Any],
) -> bool:
    common = (
        asset.get("type") == "video"
        and asset.get("verification_status") == VERIFICATION_STATUS_FILE_VERIFIED
        and provenance.get("asset_id") == asset.get("id")
        and provenance.get("result_id") == result.get("id")
        and provenance.get("derived_file_id") == derived_file.get("id")
        and provenance.get("preview_generation") == asset.get("preview_generation")
        and result.get("preview_generation") == asset.get("preview_generation")
        and derived_file.get("asset_id") == asset.get("id")
        and derived_file.get("kind") == "preview"
        and derived_file.get("mime_type") == "video/mp4"
        and provenance.get("attempt_state") == "ready"
        and provenance.get("attempt_result_id") == result.get("id")
        and provenance.get("detection_status") == asset.get("log_detection_status")
        and provenance.get("detector_rule_version")
        == asset.get("detector_rule_version")
        and provenance.get("detector_manifest_sha256")
        == asset.get("detector_manifest_sha256")
        and provenance.get("detector_evidence_sha256")
        == asset.get("detector_evidence_sha256")
    )
    apple_fallback = (
        provenance.get("detection_status") == "apple_log"
        and provenance.get("requested_preset_id")
        == "generated-apple-log-rec709"
        and provenance.get("applied_preset_id") == "compress-only"
        and provenance.get("transform_kind") == "none"
        and provenance.get("color_transform_status") == "unavailable"
        and provenance.get("color_transform_error_code")
        == "lut_preset_unavailable"
        and provenance.get("manifest_sha256") is None
        and provenance.get("lut_sha256") is None
    )
    ordinary = (
        provenance.get("detection_status") in {"not_log", "unknown"}
        and provenance.get("requested_preset_id") == "compress-only"
        and provenance.get("applied_preset_id") == "compress-only"
        and provenance.get("transform_kind") == "none"
        and provenance.get("color_transform_status") == "not_requested"
        and provenance.get("color_transform_error_code") is None
        and provenance.get("manifest_sha256") is None
        and provenance.get("lut_sha256") is None
    )
    return bool(common and (apple_fallback or ordinary))


def _passes_phase2b_forward_gate(
    *,
    asset: dict[str, Any],
    result: dict[str, Any],
    derived_file: dict[str, Any],
    formal_preview_provenance_validator: FormalPreviewProvenanceValidator | None,
) -> bool:
    """Phase 2B injects formal preview ID, generation, and provenance validation here."""
    if formal_preview_provenance_validator is None:
        return True
    return bool(formal_preview_provenance_validator(asset, result, derived_file))


def _valid_managed_provenance(
    *, conn, asset: dict[str, Any], result: dict[str, Any], derived_file: dict[str, Any]
) -> bool:
    provenance = get_rendition_provenance_by_result(conn, result_id=str(result["id"]))
    if provenance is None:
        return False
    rendition = get_rendition(conn, str(provenance["rendition_id"]))
    if rendition is None:
        return False
    return bool(
        provenance["asset_id"] == asset["id"]
        and provenance["result_id"] == result["id"]
        and provenance["derived_file_id"] == derived_file["id"]
        and rendition["asset_id"] == asset["id"]
        and rendition["result_id"] == result["id"]
        and rendition["state"] == "ready"
        and rendition["applied_preset_id"] == provenance["applied_preset_id"]
        and rendition["color_transform_status"] == provenance["color_transform_status"]
        and provenance["color_transform_status"] in {"not_requested", "unavailable", "applied"}
    )
