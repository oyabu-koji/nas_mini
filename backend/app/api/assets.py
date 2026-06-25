from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)

from app.api.deps import require_bearer_token
from app.core.settings import load_settings
from app.schemas.assets import (
    AssetListResponse,
    AssetReadResponse,
    UploadAssetResponse,
    parse_upload_metadata,
)
from app.services.asset_read import (
    AssetNotFoundError,
    PreviewNotReadyError,
    confirm_preview,
    get_asset_read,
    list_asset_reads,
)
from app.services.preview_stream import (
    InvalidRangeError,
    PreviewNotFoundError,
    PreviewNotReadyError as StreamPreviewNotReadyError,
    PreviewStorageError,
    open_preview_stream,
)
from app.services.upload import UploadTooLargeError, create_upload_asset


router = APIRouter(
    prefix="/assets",
    tags=["assets"],
    dependencies=[Depends(require_bearer_token)],
)


@router.post(
    "/upload",
    response_model=UploadAssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_asset(
    file: Annotated[UploadFile, File()],
    asset_type: Annotated[str, Form(alias="type")],
    filename: Annotated[str, Form()],
    taken_at: Annotated[str | None, Form()] = None,
    latitude: Annotated[str | None, Form()] = None,
    longitude: Annotated[str | None, Form()] = None,
    exif_json: Annotated[str | None, Form()] = None,
    is_log: Annotated[str | None, Form()] = None,
) -> UploadAssetResponse:
    try:
        metadata = parse_upload_metadata(
            asset_type=asset_type,
            filename=filename,
            taken_at=taken_at,
            latitude=latitude,
            longitude=longitude,
            exif_json=exif_json,
            is_log=is_log,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    try:
        return await create_upload_asset(
            settings=load_settings(),
            upload_file=file,
            metadata=metadata,
        )
    except UploadTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Upload exceeds maximum size",
        ) from exc


@router.get("", response_model=AssetListResponse)
def list_assets(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AssetListResponse:
    return list_asset_reads(
        settings=load_settings(),
        limit=limit,
        offset=offset,
    )


@router.get("/{asset_id}", response_model=AssetReadResponse)
def get_asset_detail(asset_id: int) -> AssetReadResponse:
    try:
        return get_asset_read(settings=load_settings(), asset_id=asset_id)
    except AssetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        ) from exc


@router.get("/{asset_id}/preview")
def stream_asset_preview(
    asset_id: int,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
):
    try:
        return open_preview_stream(
            settings=load_settings(),
            asset_id=asset_id,
            range_header=range_header,
        )
    except PreviewNotFoundError as exc:
        detail = "Asset not found" if str(exc) == "asset not found" else "Preview not found"
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
        ) from exc
    except StreamPreviewNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Preview is not ready",
        ) from exc
    except InvalidRangeError as exc:
        return Response(
            content="Invalid range",
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{exc.total_size}"},
            media_type="text/plain",
        )
    except PreviewStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Preview storage failure",
        ) from exc


@router.post("/{asset_id}/preview-confirmation", response_model=AssetReadResponse)
def confirm_asset_preview(asset_id: int) -> AssetReadResponse:
    try:
        return confirm_preview(settings=load_settings(), asset_id=asset_id)
    except AssetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        ) from exc
    except PreviewNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Preview is not ready",
        ) from exc
