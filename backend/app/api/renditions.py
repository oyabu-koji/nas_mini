from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.deps import require_bearer_token
from app.core.settings import load_settings
from app.schemas.renditions import CreateRenditionRequest, RenditionResponse
from app.services.rendition_creation import (
    RenditionCreationError,
    create_rendition,
    get_rendition_representation,
)


router = APIRouter(
    prefix="/api/v1/assets",
    tags=["renditions"],
    dependencies=[Depends(require_bearer_token)],
)


@router.post("/{asset_id}/renditions")
def post_rendition(asset_id: int, request: CreateRenditionRequest):
    try:
        result = create_rendition(
            settings=load_settings(),
            asset_id=asset_id,
            client_request_id=request.client_rendition_request_id,
            preset_id=request.preset_id,
        )
    except RenditionCreationError as exc:
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "retryable": exc.retryable},
        )
    return JSONResponse(
        status_code=200 if result.replayed else 202,
        content=RenditionResponse.model_validate(result.representation).model_dump(mode="json"),
    )


@router.get("/{asset_id}/renditions/{rendition_id}", response_model=RenditionResponse)
def get_rendition(asset_id: int, rendition_id: str):
    try:
        return get_rendition_representation(
            settings=load_settings(), asset_id=asset_id, rendition_id=rendition_id
        )
    except RenditionCreationError as exc:
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "retryable": exc.retryable},
        )
