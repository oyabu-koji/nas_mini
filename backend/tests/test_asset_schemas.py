import pytest
from pydantic import ValidationError

from app.schemas.assets import AssetResponse, parse_upload_metadata


def _asset_response(original_path: str) -> AssetResponse:
    return AssetResponse(
        id=1,
        type="image",
        filename="photo.jpg",
        original_path=original_path,
        size_bytes=10,
        server_sha256="abc123",
        taken_at=None,
        latitude=None,
        longitude=None,
        exif_json=None,
        is_log=False,
        transfer_status="uploaded",
        verification_status="server_hash_recorded",
        preview_status="preview_generating",
        review_status="not_reviewed",
        delete_candidate_status="not_candidate",
    )


def test_asset_response_accepts_relative_original_path():
    response = _asset_response("originals/generated.jpg")

    assert response.original_path == "originals/generated.jpg"


def test_asset_response_rejects_host_absolute_path():
    with pytest.raises(ValidationError):
        _asset_response("/Users/oyabu/media/originals/generated.jpg")


def test_asset_response_rejects_tmp_path():
    with pytest.raises(ValidationError):
        _asset_response("tmp/generated.upload")


def test_asset_response_rejects_originals_directory_only():
    with pytest.raises(ValidationError):
        _asset_response("originals")


def test_asset_response_rejects_path_traversal():
    with pytest.raises(ValidationError):
        _asset_response("originals/../secret.jpg")


def test_parse_upload_metadata_rejects_invalid_latitude():
    with pytest.raises(ValueError):
        parse_upload_metadata(
            asset_type="image",
            filename="photo.jpg",
            taken_at=None,
            latitude="north",
            longitude=None,
            exif_json=None,
            is_log=None,
        )


def test_parse_upload_metadata_rejects_invalid_is_log():
    with pytest.raises(ValueError):
        parse_upload_metadata(
            asset_type="image",
            filename="photo.jpg",
            taken_at=None,
            latitude=None,
            longitude=None,
            exif_json=None,
            is_log="yes",
        )
