from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RenditionState = Literal[
    "queued", "validating", "rendering", "finalizing", "ready", "failed", "superseded"
]


class CreateRenditionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    client_rendition_request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    preset_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class RenditionResponse(BaseModel):
    rendition_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    asset_id: int = Field(gt=0)
    client_rendition_request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    selection_generation: int = Field(ge=1)
    requested_preset_id: str
    applied_preset_id: str | None
    state: RenditionState
    color_transform_status: Literal["not_requested", "unavailable", "applied", "failed"] | None
    error_code: str | None
    result_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    created_at: str
    updated_at: str
