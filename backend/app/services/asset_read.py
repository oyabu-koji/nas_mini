from typing import Any

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
    AssetListResponse,
    AssetReadResponse,
    PreviewMetadataResponse,
    exif_json_from_text,
)
from app.services.storage import StorageError, resolve_media_path


class AssetNotFoundError(RuntimeError):
    pass


class PreviewNotReadyError(RuntimeError):
    pass


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
            build_asset_read_response(
                asset=asset,
                preview=get_preview_for_asset(conn, int(asset["id"])),
            )
            for asset in assets
        ]
    return AssetListResponse(items=items, limit=limit, offset=offset, total=total)


def get_asset_read(settings: Settings, *, asset_id: int) -> AssetReadResponse:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        asset = get_asset(conn, asset_id)
        if asset is None:
            raise AssetNotFoundError("asset not found")
        preview = get_preview_for_asset(conn, asset_id)
    return build_asset_read_response(asset=asset, preview=preview)


def confirm_preview(settings: Settings, *, asset_id: int) -> AssetReadResponse:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        asset = get_asset(conn, asset_id)
        if asset is None:
            raise AssetNotFoundError("asset not found")
        if asset["preview_status"] != PREVIEW_STATUS_PREVIEW_READY:
            raise PreviewNotReadyError("preview is not ready")

        preview = get_preview_for_asset(conn, asset_id)
        _validate_confirmable_preview(settings, preview)

        updated_asset = update_review_status(
            conn,
            asset_id,
            REVIEW_STATUS_PREVIEW_CONFIRMED,
        )
        if updated_asset is None:
            raise AssetNotFoundError("asset not found")
        updated_preview = get_preview_for_asset(conn, asset_id)

    return build_asset_read_response(asset=updated_asset, preview=updated_preview)


def build_asset_read_response(
    *,
    asset: dict[str, Any],
    preview: dict[str, Any] | None,
) -> AssetReadResponse:
    asset_id = int(asset["id"])
    preview_response = _build_preview_metadata(asset_id, preview)

    return AssetReadResponse(
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
