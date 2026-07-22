from dataclasses import dataclass
from typing import Any, Callable

from app.core.settings import Settings
from app.repositories.assets import PREVIEW_STATUS_PREVIEW_READY, VERIFICATION_STATUS_FILE_VERIFIED
from app.repositories.derived_files import get_derived_file
from app.repositories.processed_results import (
    get_active_processed_result,
    is_phase2a_session_video_asset,
)
from app.repositories.rendition_provenance import get_rendition_provenance_by_result
from app.repositories.renditions import get_rendition
from app.services.processed_result_integrity import (
    ProcessedResultIntegrityError,
    VerifiedProcessedResult,
    verify_processed_result,
)


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
    """Return only an active, Phase 2A eligible result whose bytes still verify."""
    if not is_phase2a_deliverable_asset(conn=conn, asset=asset):
        return None
    active_result = get_active_processed_result(conn, asset_id=int(asset["id"]))
    if active_result is None:
        return None
    derived_file_id = active_result.get("derived_file_id")
    if not isinstance(derived_file_id, int):
        return None
    derived_file = get_derived_file(conn, derived_file_id)
    if derived_file is None:
        return None
    if derived_file.get("kind") == "rendition" and not _valid_managed_provenance(
        conn=conn,
        asset=asset,
        result=active_result,
        derived_file=derived_file,
    ):
        return None
    if derived_file.get("kind") not in {"preview", "rendition"}:
        return None
    if not _passes_phase2b_forward_gate(
        asset=asset,
        result=active_result,
        derived_file=derived_file,
        formal_preview_provenance_validator=formal_preview_provenance_validator,
    ):
        return None
    try:
        verified_file = verify_processed_result(
            settings=settings,
            result=active_result,
            derived_file=derived_file,
        )
    except ProcessedResultIntegrityError:
        return None
    return DeliverableProcessedResult(
        result=active_result,
        derived_file=derived_file,
        verified_file=verified_file,
    )


def is_phase2a_deliverable_asset(*, conn, asset: dict[str, Any]) -> bool:
    return (
        asset.get("type") == "video"
        and asset.get("verification_status") == VERIFICATION_STATUS_FILE_VERIFIED
        and asset.get("preview_status") == PREVIEW_STATUS_PREVIEW_READY
        and not bool(asset.get("is_log"))
        and is_phase2a_session_video_asset(conn, asset_id=int(asset["id"]))
    )


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
