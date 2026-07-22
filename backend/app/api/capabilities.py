from fastapi import APIRouter, Depends

from app.api.deps import require_bearer_token
from app.core.settings import load_settings
from app.schemas.presets import CapabilitiesResponse, FeatureFlagsResponse
from app.services.preset_registry import custom_lut_capability


router = APIRouter(
    prefix="/api/v1/capabilities",
    tags=["capabilities"],
    dependencies=[Depends(require_bearer_token)],
)


@router.get("", response_model=CapabilitiesResponse)
def get_capabilities() -> CapabilitiesResponse:
    settings = load_settings()
    return CapabilitiesResponse(
        features=FeatureFlagsResponse(custom_lut=custom_lut_capability(settings))
    )
