import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.formal_preview_authority import (
    has_allowed_formal_transform_claim,
)


AssetType = Literal["image", "video"]
DeleteCandidateStatus = Literal[
    "not_candidate",
    "safe_to_delete_candidate",
]
TAKEN_AT_PATTERN = re.compile(
    r"^(?P<datetime>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?P<offset>Z|[+-]\d{2}:\d{2})?$"
)


class UploadMetadata(BaseModel):
    type: AssetType
    filename: str
    taken_at: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    exif_json: Any | None = None
    is_log: bool = False

    @field_validator("taken_at", mode="before")
    @classmethod
    def validate_taken_at(cls, value: str | None) -> str | None:
        return normalize_taken_at(value)


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    filename: str
    original_path: str
    size_bytes: int
    server_sha256: str
    taken_at: str | None
    latitude: float | None
    longitude: float | None
    exif_json: Any | None
    is_log: bool
    transfer_status: str
    verification_status: str
    preview_status: str
    review_status: str
    delete_candidate_status: DeleteCandidateStatus

    @field_validator("original_path")
    @classmethod
    def validate_original_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        parts = path.parts
        if path.is_absolute() or not parts:
            raise ValueError("original_path must be a relative path")
        if parts[0] != "originals" or len(parts) < 2:
            raise ValueError("original_path must be under originals")
        if ".." in parts:
            raise ValueError("original_path cannot contain traversal")
        return value


class PreviewMetadataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    mime_type: str | None
    size_bytes: int | None
    url: str
    created_at: str


class AssetReadBaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    filename: str
    size_bytes: int
    server_sha256: str
    taken_at: str | None
    latitude: float | None
    longitude: float | None
    exif_json: Any | None
    is_log: bool
    transfer_status: str
    verification_status: str
    preview_status: str
    review_status: str
    delete_candidate_status: DeleteCandidateStatus
    created_at: str
    updated_at: str
    preview: PreviewMetadataResponse | None


class ProcessedResultMetadataResponse(BaseModel):
    result_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    mime_type: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: str
    url: str


FormalPreviewFailureCode = Literal[
    "log_detector_manifest_invalid",
    "log_detector_version_mismatch",
    "log_probe_timeout",
    "log_probe_failed",
    "log_probe_output_invalid",
    "log_container_invalid",
    "log_container_resource_limit",
    "log_container_source_changed",
    "lut_preset_registered_invalid",
    "lut_preset_source_changed",
    "lut_application_failed",
    "formal_preview_source_invalid",
    "formal_preview_render_failed",
    "formal_preview_storage_failed",
    "formal_preview_database_failed",
    "formal_preview_relation_invalid",
]
DetectionStatus = Literal["apple_log", "not_log", "unknown"]
SourceProfile = Literal["apple-log-1", "apple-log-2"]
TransformKind = Literal["none", "lut"]
ColorTransformStatus = Literal["not_requested", "unavailable", "applied", "failed"]
PRESET_ID_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class FormalPreviewBaseResponse(BaseModel):
    schema_version: Literal[1] = 1
    state: str
    generation: int = Field(ge=1)


class FormalPreviewGeneratingResponse(FormalPreviewBaseResponse):
    state: Literal["generating"]
    detection_status: DetectionStatus | None = None
    source_profile: SourceProfile | None = None
    detector_rule_version: str | None = Field(default=None, min_length=1, max_length=64)
    detector_manifest_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    detector_evidence_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    requested_preset_id: str | None = Field(default=None, pattern=PRESET_ID_PATTERN)
    applied_preset_id: str | None = Field(default=None, pattern=PRESET_ID_PATTERN)
    applied_preset_display_name: str | None = None
    preset_version: str | None = None
    manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    lut_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    transform_kind: TransformKind | None = None
    color_transform_status: ColorTransformStatus | None = None
    color_transform_error_code: str | None = None
    preview_id: None = None
    result: None = None
    failure_code: None = None

    @model_validator(mode="after")
    def validate_partial_groups(self):
        _validate_detector_group(self)
        _validate_requested_preset_relation(self)
        values = self.model_dump()
        transform_empty = all(
            values.get(field) is None
            for field in (
                "applied_preset_id",
                "applied_preset_display_name",
                "preset_version",
                "manifest_sha256",
                "lut_sha256",
                "transform_kind",
                "color_transform_status",
                "color_transform_error_code",
            )
        )
        if self.requested_preset_id is None:
            if not transform_empty:
                raise ValueError("generating transform group is invalid")
        elif not has_allowed_formal_transform_claim(values):
            raise ValueError("generating transform claim is invalid")
        return self


class FormalPreviewReadyResponse(FormalPreviewBaseResponse):
    state: Literal["ready"]
    detection_status: DetectionStatus
    source_profile: SourceProfile | None = None
    detector_rule_version: str = Field(min_length=1, max_length=64)
    detector_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    detector_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_preset_id: str = Field(pattern=PRESET_ID_PATTERN)
    applied_preset_id: str = Field(pattern=PRESET_ID_PATTERN)
    applied_preset_display_name: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    preset_version: str | None = Field(default=None, min_length=1, max_length=64)
    manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    lut_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    transform_kind: TransformKind
    color_transform_status: Literal["not_requested", "unavailable", "applied"]
    color_transform_error_code: str | None = None
    preview_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    result: ProcessedResultMetadataResponse
    failure_code: None = None

    @model_validator(mode="after")
    def validate_transform_claim(self):
        values = self.model_dump()
        display_name_is_valid = (
            self.applied_preset_display_name is not None
            if self.color_transform_status == "applied"
            else self.applied_preset_display_name is None
        )
        if not (
            display_name_is_valid
            and has_allowed_formal_transform_claim(values)
        ):
            raise ValueError("formal preview transform claim is invalid")
        return self


class FormalPreviewFailedResponse(FormalPreviewBaseResponse):
    state: Literal["failed"]
    detection_status: DetectionStatus | None = None
    source_profile: SourceProfile | None = None
    detector_rule_version: str | None = Field(default=None, min_length=1, max_length=64)
    detector_manifest_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    detector_evidence_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    requested_preset_id: str | None = Field(default=None, pattern=PRESET_ID_PATTERN)
    applied_preset_id: None = None
    applied_preset_display_name: None = None
    preset_version: None = None
    manifest_sha256: None = None
    lut_sha256: None = None
    transform_kind: TransformKind | None = None
    color_transform_status: Literal["failed"] | None = None
    color_transform_error_code: str | None = None
    preview_id: None = None
    result: None = None
    failure_code: FormalPreviewFailureCode

    @model_validator(mode="after")
    def validate_partial_groups(self):
        _validate_detector_group(self)
        _validate_requested_preset_relation(self)
        if (self.transform_kind is None) != (self.color_transform_status is None):
            raise ValueError("failed transform group must be all present or all null")
        if self.requested_preset_id is None:
            if self.transform_kind is not None or self.color_transform_error_code is not None:
                raise ValueError("failed transform group requires requested preset")
        elif self.transform_kind is None or self.color_transform_status != "failed":
            raise ValueError("failed requested preset requires transform failure")
        return self


FormalPreviewResponse = Annotated[
    FormalPreviewGeneratingResponse
    | FormalPreviewReadyResponse
    | FormalPreviewFailedResponse,
    Field(discriminator="state"),
]


class AssetListItemResponse(AssetReadBaseResponse):
    pass


class AssetDetailResponse(AssetReadBaseResponse):
    active_processed_result: ProcessedResultMetadataResponse | None
    formal_preview: FormalPreviewResponse | None = None


# Kept as an import-compatible name for existing callers while list and detail
# now have separate response models.
AssetReadResponse = AssetDetailResponse


class AssetListResponse(BaseModel):
    items: list[AssetListItemResponse]
    limit: int
    offset: int
    total: int


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_type: str
    status: str
    asset_id: int | None


class UploadAssetResponse(BaseModel):
    asset: AssetResponse
    job: JobResponse
    server_sha256: str
    transfer_status: str
    verification_status: str
    preview_status: str
    review_status: str
    delete_candidate_status: DeleteCandidateStatus


def _validate_detector_group(value) -> None:
    identity = (
        value.detector_rule_version,
        value.detector_manifest_sha256,
        value.detector_evidence_sha256,
    )
    if value.detection_status is None:
        if value.source_profile is not None or any(item is not None for item in identity):
            raise ValueError("detector group must be all present or all null")
    elif any(item is None for item in identity):
        raise ValueError("detector group must be all present or all null")
    elif (
        value.detection_status == "apple_log"
        and value.source_profile not in {"apple-log-1", "apple-log-2"}
    ) or (
        value.detection_status in {"not_log", "unknown"}
        and value.source_profile is not None
    ):
        raise ValueError("detector status/profile relation is invalid")


def _validate_requested_preset_relation(value) -> None:
    requested = value.requested_preset_id
    if requested is None:
        return
    expected = {
        ("apple_log", "apple-log-1"): "generated-apple-log-rec709",
        ("apple_log", "apple-log-2"): "generated-apple-log2-rec709",
        ("not_log", None): "compress-only",
        ("unknown", None): "compress-only",
    }.get((value.detection_status, value.source_profile))
    if requested != expected:
        raise ValueError("detector profile/requested preset relation is invalid")


def parse_upload_metadata(
    *,
    asset_type: str,
    filename: str,
    taken_at: str | None,
    latitude: str | None,
    longitude: str | None,
    exif_json: str | None,
    is_log: str | None,
) -> UploadMetadata:
    normalized_type = asset_type.strip()
    if normalized_type not in ("image", "video"):
        raise ValueError("type must be image or video")

    normalized_filename = filename.strip()
    if not normalized_filename:
        raise ValueError("filename is required")

    return UploadMetadata(
        type=normalized_type,  # type: ignore[arg-type]
        filename=normalized_filename,
        taken_at=taken_at,
        latitude=_parse_optional_float(latitude, "latitude"),
        longitude=_parse_optional_float(longitude, "longitude"),
        exif_json=_parse_optional_json(exif_json),
        is_log=_parse_optional_bool(is_log),
    )


def exif_json_to_text(value: Any | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def exif_json_from_text(value: str | None) -> Any | None:
    if value is None or value == "":
        return None
    return json.loads(value)


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def normalize_taken_at(value: str | None) -> str | None:
    normalized = _blank_to_none(value)
    if normalized is None:
        return None

    match = TAKEN_AT_PATTERN.fullmatch(normalized)
    if match is None:
        raise ValueError("taken_at must be a seconds-precision ISO 8601 datetime")

    offset = match.group("offset")
    canonical = normalized
    try:
        if offset is None:
            datetime.strptime(canonical, "%Y-%m-%dT%H:%M:%S")
        else:
            if offset == "Z":
                canonical = f"{match.group('datetime')}+00:00"
            datetime.strptime(canonical, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError as exc:
        raise ValueError("taken_at must be a valid ISO 8601 datetime") from exc
    return canonical


def _parse_optional_float(value: str | None, field_name: str) -> float | None:
    normalized = _blank_to_none(value)
    if normalized is None:
        return None
    try:
        return float(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a number") from exc


def _parse_optional_json(value: str | None) -> Any | None:
    normalized = _blank_to_none(value)
    if normalized is None:
        return None
    try:
        return json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise ValueError("exif_json must be valid JSON") from exc


def _parse_optional_bool(value: str | None) -> bool:
    normalized = _blank_to_none(value)
    if normalized is None:
        return False
    lowered = normalized.lower()
    if lowered in ("true", "1"):
        return True
    if lowered in ("false", "0"):
        return False
    raise ValueError("is_log must be true, false, 1, or 0")
