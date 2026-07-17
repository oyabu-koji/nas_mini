import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator


AssetType = Literal["image", "video"]
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
    delete_candidate_status: str

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


class AssetReadResponse(BaseModel):
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
    delete_candidate_status: str
    created_at: str
    updated_at: str
    preview: PreviewMetadataResponse | None


class AssetListResponse(BaseModel):
    items: list[AssetReadResponse]
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
    delete_candidate_status: str


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
