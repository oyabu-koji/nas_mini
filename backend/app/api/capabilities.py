from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.api.deps import require_bearer_token
from app.core.settings import load_settings
from app.schemas.presets import CapabilitiesResponse, FeatureFlagsResponse
from app.services.preset_registry import custom_lut_capability
from app.db.phase_schema_identity import PhaseSchemaIdentityError
from app.services.phase2_rollout import resolve_phase2_rollout


router = APIRouter(
    prefix="/api/v1/capabilities",
    tags=["capabilities"],
    dependencies=[Depends(require_bearer_token)],
)


@router.get("", response_model=CapabilitiesResponse)
def get_capabilities() -> CapabilitiesResponse | JSONResponse:
    settings = load_settings()
    try:
        capability = resolve_phase2_rollout(settings=settings)
    except PhaseSchemaIdentityError as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"code": exc.code, "retryable": False},
        )
    return CapabilitiesResponse(
        minimum_client_version=capability.minimum_client_version,
        features=FeatureFlagsResponse(
            custom_lut=custom_lut_capability(settings),
            detector_certified=capability.detector_certified,
            formal_apple_log_preview=capability.formal_apple_log_preview,
            safe_delete_candidate=capability.safe_delete_candidate,
        ),
    )
