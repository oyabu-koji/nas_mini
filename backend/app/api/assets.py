from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import require_bearer_token
from app.core.settings import load_settings
from app.schemas.assets import UploadAssetResponse, parse_upload_metadata
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
