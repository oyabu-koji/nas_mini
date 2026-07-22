from fastapi import APIRouter, Depends

from app.api.deps import require_bearer_token
from app.core.settings import load_settings
from app.schemas.presets import PresetCatalogResponse
from app.services.preset_registry import list_available_presets


router = APIRouter(
    prefix="/api/v1/presets",
    tags=["presets"],
    dependencies=[Depends(require_bearer_token)],
)


@router.get("", response_model=PresetCatalogResponse)
def get_presets() -> PresetCatalogResponse:
    return PresetCatalogResponse(items=list_available_presets(load_settings()))
