from typing import get_args

import pytest
from pydantic import ValidationError

from app.schemas.assets import (
    AssetReadBaseResponse,
    AssetResponse,
    DeleteCandidateStatus,
    UploadAssetResponse,
    normalize_taken_at,
    parse_upload_metadata,
)


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


@pytest.mark.parametrize(
    "response_model",
    [AssetResponse, AssetReadBaseResponse, UploadAssetResponse],
)
def test_asset_response_candidate_status_is_a_closed_enum(response_model):
    annotation = response_model.model_fields["delete_candidate_status"].annotation

    assert set(get_args(annotation)) == set(get_args(DeleteCandidateStatus))


def test_asset_response_rejects_unknown_candidate_status():
    response = _asset_response("originals/generated.jpg")

    with pytest.raises(ValidationError):
        AssetResponse.model_validate(
            {
                **response.model_dump(),
                "delete_candidate_status": "unexpected",
            }
        )


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-11T12:34:56", "2026-07-11T12:34:56"),
        ("2026-07-11T12:34:56Z", "2026-07-11T12:34:56+00:00"),
        ("2026-07-11T12:34:56+09:00", "2026-07-11T12:34:56+09:00"),
        ("2026-07-11T12:34:56-05:00", "2026-07-11T12:34:56-05:00"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_taken_at_accepts_only_canonical_seconds_precision(value, expected):
    assert normalize_taken_at(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-11",
        "2026-07-11 12:34:56",
        "2026-07-11T12:34:56.123",
        "+09:00",
        "null",
        "2026-02-30T12:34:56",
        "2026-07-11T12:34:56+24:00",
    ],
)
def test_normalize_taken_at_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        normalize_taken_at(value)
