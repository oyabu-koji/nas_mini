from fastapi import APIRouter, Depends

from app.api.deps import require_bearer_token
from app.core.settings import load_settings
from app.schemas.presets import CapabilitiesResponse, FeatureFlagsResponse
from app.services.preset_registry import custom_lut_capability
from app.services.detector_capability import evaluate_detector_capability


router = APIRouter(
    prefix="/api/v1/capabilities",
    tags=["capabilities"],
    dependencies=[Depends(require_bearer_token)],
)


@router.get("", response_model=CapabilitiesResponse)
def get_capabilities() -> CapabilitiesResponse:
    settings = load_settings()
    capability = evaluate_detector_capability(settings)
    return CapabilitiesResponse(
        minimum_client_version="0.2.0" if capability.formal_apple_log_preview else None,
        features=FeatureFlagsResponse(
            custom_lut=custom_lut_capability(settings),
            detector_certified=capability.detector_certified,
            formal_apple_log_preview=capability.formal_apple_log_preview,
        ),
    )
