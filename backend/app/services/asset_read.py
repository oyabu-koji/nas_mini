from dataclasses import dataclass
from typing import Any, Callable

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.assets import (
    PREVIEW_STATUS_PREVIEW_READY,
    REVIEW_STATUS_PREVIEW_CONFIRMED,
    count_assets,
    get_asset,
    list_assets,
    update_review_status,
)
from app.repositories.derived_files import get_preview_for_asset
from app.schemas.assets import (
    AssetDetailResponse,
    AssetListItemResponse,
    AssetListResponse,
    ProcessedResultMetadataResponse,
    PreviewMetadataResponse,
    exif_json_from_text,
)
from app.services.storage import StorageError, resolve_media_path
from app.services.processed_result_delivery import (
    DeliverableProcessedResult,
    has_valid_formal_preview_relation,
    resolve_deliverable_result,
    resolve_formal_preview_result,
)
from app.services.formal_preview_read import build_formal_preview_response
from app.services.safe_delete_candidate import (
    evaluate_safe_delete_candidate,
    project_candidate_status,
)


class AssetNotFoundError(RuntimeError):
    pass


class PreviewNotReadyError(RuntimeError):
    pass


class PreviewProvenanceInvalidError(RuntimeError):
    pass


@dataclass(frozen=True)
class FormalPreviewIntegritySnapshot:
    result_id: str
    derived_file_id: int
    relative_path: str
    mime_type: str
    size_bytes: int
    sha256: str
    asset_id: int
    preview_generation: int


@dataclass(frozen=True)
class FormalPreviewConfirmationPreflight:
    snapshot: FormalPreviewIntegritySnapshot
    formal_preview: Any
    delivery: DeliverableProcessedResult


ConfirmationFaultInjector = Callable[[str], None]


def list_asset_reads(
    settings: Settings,
    *,
    limit: int,
    offset: int,
) -> AssetListResponse:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        assets = list_assets(conn, limit=limit, offset=offset)
        total = count_assets(conn)
        items = [
            build_asset_list_item_response(
                asset=asset,
                preview=get_preview_for_asset(conn, int(asset["id"])),
            )
            for asset in assets
        ]
    return AssetListResponse(items=items, limit=limit, offset=offset, total=total)


def get_asset_read(settings: Settings, *, asset_id: int) -> AssetDetailResponse:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        asset = get_asset(conn, asset_id)
        if asset is None:
            raise AssetNotFoundError("asset not found")
        preview = get_preview_for_asset(conn, asset_id)
        deliverable_result = resolve_deliverable_result(
            settings=settings,
            conn=conn,
            asset=asset,
        )
        formal_preview = build_formal_preview_response(
            settings=settings,
            conn=conn,
            asset=asset,
        )
    return build_asset_detail_response(
        asset=asset,
        preview=preview,
        active_processed_result=deliverable_result,
        formal_preview=formal_preview,
    )


def confirm_preview(
    settings: Settings,
    *,
    asset_id: int,
    allow_candidate_promotion: bool = False,
    fault_injector: ConfirmationFaultInjector | None = None,
) -> AssetDetailResponse:
    preflight = _preflight_confirmation(
        settings=settings,
        asset_id=asset_id,
    )
    _inject(fault_injector, "after_preflight")
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            asset = get_asset(conn, asset_id)
            if asset is None:
                raise AssetNotFoundError("asset not found")
            is_phase2b = "formal_preview_id" in asset
            if is_phase2b:
                if preflight is None:
                    raise PreviewProvenanceInvalidError()
                if (
                    not has_valid_formal_preview_relation(conn=conn, asset=asset)
                    or not _snapshot_matches(
                        conn,
                        asset=asset,
                        snapshot=preflight.snapshot,
                    )
                ):
                    raise PreviewProvenanceInvalidError()
            elif (
                preflight is not None
                or bool(asset["is_log"])
                or asset["preview_status"] != PREVIEW_STATUS_PREVIEW_READY
            ):
                raise PreviewNotReadyError("preview is not ready")

            if (
                is_phase2b
                and asset.get("delete_candidate_status")
                == "safe_to_delete_candidate"
            ):
                current_evaluation = evaluate_safe_delete_candidate(
                    conn,
                    asset_id=asset_id,
                )
                if not current_evaluation.eligible:
                    project_candidate_status(
                        conn,
                        asset_id=asset_id,
                        evaluation=current_evaluation,
                        allow_promotion=False,
                    )

            updated_asset = update_review_status(
                conn,
                asset_id,
                REVIEW_STATUS_PREVIEW_CONFIRMED,
            )
            if updated_asset is None:
                raise AssetNotFoundError("asset not found")
            _inject(fault_injector, "after_review")
            if is_phase2b:
                evaluation = evaluate_safe_delete_candidate(
                    conn,
                    asset_id=asset_id,
                )
                project_candidate_status(
                    conn,
                    asset_id=asset_id,
                    evaluation=evaluation,
                    allow_promotion=allow_candidate_promotion,
                )
                updated_asset = get_asset(conn, asset_id)
                if updated_asset is None:
                    raise AssetNotFoundError("asset not found")
            _inject(fault_injector, "after_candidate")
            updated_preview = get_preview_for_asset(conn, asset_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return build_asset_detail_response(
        asset=updated_asset,
        preview=updated_preview,
        active_processed_result=(
            preflight.delivery
            if preflight is not None
            and updated_asset.get("active_processed_result_id")
            == preflight.snapshot.result_id
            else None
        ),
        formal_preview=preflight.formal_preview if preflight is not None else None,
    )


def _preflight_confirmation(
    *,
    settings: Settings,
    asset_id: int,
) -> FormalPreviewConfirmationPreflight | None:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        asset = get_asset(conn, asset_id)
        if asset is None:
            raise AssetNotFoundError("asset not found")
        preview = get_preview_for_asset(conn, asset_id)
        if "formal_preview_id" not in asset:
            if (
                bool(asset["is_log"])
                or asset["preview_status"] != PREVIEW_STATUS_PREVIEW_READY
            ):
                raise PreviewNotReadyError("preview is not ready")
            _validate_confirmable_preview(settings, preview)
            return None
        delivery = resolve_formal_preview_result(
            settings=settings,
            conn=conn,
            asset=asset,
        )
        if delivery is None:
            if (
                asset.get("preview_status") == PREVIEW_STATUS_PREVIEW_READY
                and asset.get("formal_preview_id") is not None
            ):
                raise PreviewProvenanceInvalidError()
            raise PreviewNotReadyError("preview is not ready")
        formal_preview = build_formal_preview_response(
            settings=settings,
            conn=conn,
            asset=asset,
        )
        if formal_preview is None or formal_preview.state != "ready":
            raise PreviewProvenanceInvalidError()
        result = delivery.result
        derived = delivery.derived_file
        return FormalPreviewConfirmationPreflight(
            snapshot=FormalPreviewIntegritySnapshot(
                result_id=str(result["id"]),
                derived_file_id=int(derived["id"]),
                relative_path=str(derived["path"]),
                mime_type=str(result["mime_type"]),
                size_bytes=int(result["size_bytes"]),
                sha256=str(result["sha256"]),
                asset_id=int(asset["id"]),
                preview_generation=int(asset["preview_generation"]),
            ),
            formal_preview=formal_preview,
            delivery=delivery,
        )


def _snapshot_matches(
    conn,
    *,
    asset: dict[str, Any],
    snapshot: FormalPreviewIntegritySnapshot,
) -> bool:
    row = conn.execute(
        """
        SELECT
            processed_results.id AS result_id,
            processed_results.derived_file_id,
            processed_results.mime_type,
            processed_results.size_bytes,
            processed_results.sha256,
            processed_results.asset_id,
            processed_results.preview_generation,
            derived_files.path AS relative_path,
            derived_files.mime_type AS derived_mime_type,
            derived_files.size_bytes AS derived_size_bytes,
            derived_files.asset_id AS derived_asset_id
        FROM processed_results
        JOIN derived_files
          ON derived_files.id = processed_results.derived_file_id
        WHERE processed_results.id = ?
        """,
        (asset.get("formal_preview_id"),),
    ).fetchone()
    return bool(
        row is not None
        and row["result_id"] == snapshot.result_id
        and row["derived_file_id"] == snapshot.derived_file_id
        and row["relative_path"] == snapshot.relative_path
        and row["mime_type"] == snapshot.mime_type
        and row["derived_mime_type"] == snapshot.mime_type
        and row["size_bytes"] == snapshot.size_bytes
        and row["derived_size_bytes"] == snapshot.size_bytes
        and row["sha256"] == snapshot.sha256
        and row["asset_id"] == snapshot.asset_id
        and row["derived_asset_id"] == snapshot.asset_id
        and row["preview_generation"] == snapshot.preview_generation
        and asset.get("id") == snapshot.asset_id
        and asset.get("preview_generation") == snapshot.preview_generation
    )


def _inject(injector: ConfirmationFaultInjector | None, step: str) -> None:
    if injector is not None:
        injector(step)


def build_asset_list_item_response(
    *,
    asset: dict[str, Any],
    preview: dict[str, Any] | None,
) -> AssetListItemResponse:
    asset_id = int(asset["id"])
    preview_response = (
        None
        if "formal_preview_id" not in asset and bool(asset["is_log"])
        else _build_preview_metadata(asset_id, preview)
    )

    return AssetListItemResponse(
        id=asset_id,
        type=str(asset["type"]),
        filename=str(asset["filename"]),
        size_bytes=int(asset["size_bytes"]),
        server_sha256=str(asset["server_sha256"]),
        taken_at=asset["taken_at"],
        latitude=asset["latitude"],
        longitude=asset["longitude"],
        exif_json=exif_json_from_text(asset["exif_json"]),
        is_log=bool(asset["is_log"]),
        transfer_status=str(asset["transfer_status"]),
        verification_status=str(asset["verification_status"]),
        preview_status=str(asset["preview_status"]),
        review_status=str(asset["review_status"]),
        delete_candidate_status=str(asset["delete_candidate_status"]),
        created_at=str(asset["created_at"]),
        updated_at=str(asset["updated_at"]),
        preview=preview_response,
    )


def build_asset_detail_response(
    *,
    asset: dict[str, Any],
    preview: dict[str, Any] | None,
    active_processed_result,
    formal_preview=None,
) -> AssetDetailResponse:
    list_item = build_asset_list_item_response(asset=asset, preview=preview)
    result_metadata = None
    if active_processed_result is not None:
        result = active_processed_result.result
        result_metadata = ProcessedResultMetadataResponse(
            result_id=str(result["id"]),
            mime_type=str(result["mime_type"]),
            size_bytes=int(result["size_bytes"]),
            sha256=str(result["sha256"]),
            created_at=_result_created_at(str(result["created_at"])),
            url=f"/assets/{list_item.id}/results/{result['id']}",
        )
    return AssetDetailResponse(
        **list_item.model_dump(),
        active_processed_result=result_metadata,
        formal_preview=formal_preview,
    )


def build_asset_read_response(
    *,
    asset: dict[str, Any],
    preview: dict[str, Any] | None,
) -> AssetDetailResponse:
    """Compatibility helper for callers that do not need result resolution."""
    return build_asset_detail_response(
        asset=asset,
        preview=preview,
        active_processed_result=None,
        formal_preview=None,
    )


def _build_preview_metadata(
    asset_id: int,
    preview: dict[str, Any] | None,
) -> PreviewMetadataResponse | None:
    if preview is None:
        return None
    return PreviewMetadataResponse(
        id=int(preview["id"]),
        kind=str(preview["kind"]),
        mime_type=preview["mime_type"],
        size_bytes=preview["size_bytes"],
        url=f"/assets/{asset_id}/preview",
        created_at=str(preview["created_at"]),
    )


def _validate_confirmable_preview(
    settings: Settings,
    preview: dict[str, Any] | None,
) -> None:
    if preview is None:
        raise PreviewNotReadyError("preview is not ready")

    mime_type = preview["mime_type"]
    if mime_type is None or str(mime_type).strip() == "":
        raise PreviewNotReadyError("preview is not ready")

    try:
        preview_path = resolve_media_path(settings.media_root, str(preview["path"]))
    except StorageError as exc:
        raise PreviewNotReadyError("preview is not ready") from exc

    if not preview_path.is_file():
        raise PreviewNotReadyError("preview is not ready")


def _result_created_at(value: str) -> str:
    if "T" in value:
        return value
    return value.replace(" ", "T", 1) + "Z"
