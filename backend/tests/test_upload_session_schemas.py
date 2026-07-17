import pytest
from pydantic import ValidationError

from app.schemas.upload_sessions import (
    UploadSessionCreateRequest,
    parse_content_range,
    validate_chunk_sha256,
)


def test_session_create_schema_normalizes_and_validates_immutable_metadata():
    request = UploadSessionCreateRequest(
        client_upload_id="client-upload-123",
        filename=" clip.mov ",
        size_bytes=8,
        expected_file_sha256="A" * 64,
        taken_at="2026-07-12T12:34:56Z",
    )

    assert request.filename == "clip.mov"
    assert request.expected_file_sha256 == "a" * 64
    assert request.taken_at == "2026-07-12T12:34:56+00:00"


@pytest.mark.parametrize(
    "field,value",
    [
        ("client_upload_id", "bad id"),
        ("size_bytes", 0),
        ("expected_file_sha256", "bad"),
        ("filename", " "),
    ],
)
def test_session_create_schema_rejects_invalid_values(field, value):
    data = {
        "client_upload_id": "client-upload-123",
        "filename": "clip.mov",
        "size_bytes": 8,
        "expected_file_sha256": "a" * 64,
    }
    data[field] = value

    with pytest.raises(ValidationError):
        UploadSessionCreateRequest(**data)


def test_chunk_header_parsers_require_exact_safe_values():
    assert parse_content_range("bytes 0-7/8") == (0, 7, 8)
    assert validate_chunk_sha256("A" * 64) == "a" * 64

    with pytest.raises(ValueError):
        parse_content_range("bytes 0-8/8")
    with pytest.raises(ValueError):
        validate_chunk_sha256("nope")
