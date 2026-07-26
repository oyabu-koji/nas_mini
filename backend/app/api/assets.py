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
from fastapi.responses import JSONResponse

from app.api.deps import require_bearer_token
from app.core.settings import load_settings
from app.schemas.assets import (
    AssetDetailResponse,
    AssetListResponse,
    UploadAssetResponse,
    parse_upload_metadata,
)
from app.services.asset_read import (
    AssetNotFoundError,
    PreviewNotReadyError,
    PreviewProvenanceInvalidError,
    confirm_preview,
    get_asset_read,
    list_asset_reads,
)
from app.services.preview_stream import (
    FormalPreviewProvenanceInvalidError,
    InvalidRangeError,
    PreviewNotFoundError,
    PreviewNotReadyError as StreamPreviewNotReadyError,
    PreviewStorageError,
    open_preview_stream,
)
from app.services.processed_result_stream import (
    ProcessedResultNotFoundError,
    ProcessedResultNotReadyError,
    ProcessedResultProvenanceInvalidError,
    ProcessedResultRangeNotSatisfiableError,
    ProcessedResultSupersededError,
    open_processed_result_stream,
)
from app.services.client_compatibility import (
    IncompatibleClientError,
    require_compatible_client_for_asset,
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

    if metadata.type == "video":
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "video_session_required", "retryable": False},
        )

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


@router.get("/{asset_id}/results/{result_id}")
def stream_processed_result(
    asset_id: int,
    result_id: str,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
    client_version: Annotated[
        str | None, Header(alias="X-MediaVault-Client-Version")
    ] = None,
):
    settings = load_settings()
    try:
        phase2b_asset = require_compatible_client_for_asset(
            settings=settings,
            asset_id=asset_id,
            client_version=client_version,
        )
    except IncompatibleClientError:
        return _conflict("incompatible_client")
    try:
        return open_processed_result_stream(
            settings=settings,
            asset_id=asset_id,
            result_id=result_id,
            range_header=range_header,
        )
    except ProcessedResultNotFoundError:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"code": "processed_result_not_found", "retryable": False},
        )
    except ProcessedResultSupersededError:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "processed_result_superseded", "retryable": False},
        )
    except ProcessedResultNotReadyError:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "processed_result_not_ready", "retryable": False},
        )
    except ProcessedResultProvenanceInvalidError:
        return _conflict("formal_preview_provenance_invalid")
    except ProcessedResultRangeNotSatisfiableError as exc:
        return JSONResponse(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            content={"code": "processed_result_range_not_satisfiable", "retryable": False},
            headers={"Content-Range": f"bytes */{exc.total_size}"},
        )


@router.get("/{asset_id}", response_model=AssetDetailResponse)
def get_asset_detail(asset_id: int) -> AssetDetailResponse:
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
    client_version: Annotated[
        str | None, Header(alias="X-MediaVault-Client-Version")
    ] = None,
):
    settings = load_settings()
    try:
        phase2b_asset = require_compatible_client_for_asset(
            settings=settings,
            asset_id=asset_id,
            client_version=client_version,
        )
    except IncompatibleClientError:
        return _conflict("incompatible_client")
    try:
        return open_preview_stream(
            settings=settings,
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
        if phase2b_asset:
            return _conflict("formal_preview_not_ready")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Preview is not ready",
        ) from exc
    except FormalPreviewProvenanceInvalidError:
        return _conflict("formal_preview_provenance_invalid")
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


@router.post("/{asset_id}/preview-confirmation", response_model=AssetDetailResponse)
def confirm_asset_preview(
    asset_id: int,
    client_version: Annotated[
        str | None, Header(alias="X-MediaVault-Client-Version")
    ] = None,
):
    settings = load_settings()
    try:
        phase2b_asset = require_compatible_client_for_asset(
            settings=settings,
            asset_id=asset_id,
            client_version=client_version,
        )
    except IncompatibleClientError:
        return _conflict("incompatible_client")
    try:
        return confirm_preview(settings=settings, asset_id=asset_id)
    except AssetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        ) from exc
    except PreviewNotReadyError as exc:
        if phase2b_asset:
            return _conflict("formal_preview_not_ready")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Preview is not ready",
        ) from exc
    except PreviewProvenanceInvalidError:
        return _conflict("formal_preview_provenance_invalid")


def _conflict(code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"code": code, "retryable": False},
    )
