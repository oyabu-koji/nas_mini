from pydantic import BaseModel, ConfigDict


class FeatureFlagsResponse(BaseModel):
    processed_result_delivery: bool = True
    managed_preview_presets: bool = True
    custom_lut: bool
    generated_apple_log_conversion: bool = False
    numeric_rendition_progress: bool = False


class CapabilitiesResponse(BaseModel):
    api_version: str = "v1"
    minimum_client_version: str | None = None
    features: FeatureFlagsResponse


class PresetResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    preset_id: str
    display_name: str
    preset_kind: str
    enabled: bool
    available: bool
    version: str
    target_color_space: str | None
    source_reference: str
    terms_reference: str


class PresetCatalogResponse(BaseModel):
    items: list[PresetResponse]
