import json
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator


AssetType = Literal["image", "video"]


class UploadMetadata(BaseModel):
    type: AssetType
    filename: str
    taken_at: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    exif_json: Any | None = None
    is_log: bool = False


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
        taken_at=_blank_to_none(taken_at),
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
