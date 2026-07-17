import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.assets import normalize_taken_at


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CLIENT_UPLOAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")


class UploadSessionCreateRequest(BaseModel):
    client_upload_id: str
    filename: str = Field(min_length=1, max_length=512)
    size_bytes: int = Field(gt=0)
    expected_file_sha256: str
    taken_at: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    exif_json: Any | None = None
    is_log: bool = False

    @field_validator("client_upload_id")
    @classmethod
    def validate_client_upload_id(cls, value: str) -> str:
        if not CLIENT_UPLOAD_ID_PATTERN.fullmatch(value):
            raise ValueError("client_upload_id must be an opaque identifier")
        return value

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("filename is required")
        return normalized

    @field_validator("expected_file_sha256")
    @classmethod
    def validate_expected_file_sha256(cls, value: str) -> str:
        normalized = value.lower()
        if not SHA256_PATTERN.fullmatch(normalized):
            raise ValueError("expected_file_sha256 must be a lowercase SHA-256 digest")
        return normalized

    @field_validator("taken_at", mode="before")
    @classmethod
    def validate_taken_at(cls, value: str | None) -> str | None:
        return normalize_taken_at(value)


class UploadChunkResponse(BaseModel):
    chunk_index: int
    start_offset: int
    end_offset: int
    size_bytes: int
    sha256: str


class UploadSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    size_bytes: int
    chunk_size_bytes: int
    total_chunks: int
    expected_file_sha256: str
    expires_at: str
    missing_chunk_indexes: list[int]
    retryable: bool
    failure_code: str | None
    asset_id: int | None
    finalization_job_id: int | None


class UploadSessionErrorResponse(BaseModel):
    code: str
    retryable: bool = False
    retry_after_seconds: int | None = None


class UploadSessionFinalizeResponse(BaseModel):
    session: UploadSessionResponse
    job_id: int | None
    asset_id: int | None
    preview_job_id: int | None


ContentRange = tuple[int, int, int]


def parse_content_range(value: str | None) -> ContentRange:
    if value is None:
        raise ValueError("Content-Range is required")
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", value.strip())
    if match is None:
        raise ValueError("Content-Range is invalid")
    start, end, total = (int(group) for group in match.groups())
    if end < start or total <= end:
        raise ValueError("Content-Range is invalid")
    return start, end, total


def validate_chunk_sha256(value: str | None) -> str:
    normalized = str(value or "").lower()
    if not SHA256_PATTERN.fullmatch(normalized):
        raise ValueError("X-Chunk-SHA256 must be a lowercase SHA-256 digest")
    return normalized
