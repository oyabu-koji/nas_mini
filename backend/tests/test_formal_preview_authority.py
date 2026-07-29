import pytest

from app.services.formal_preview_authority import (
    has_allowed_formal_transform_claim,
)


def _claim(**overrides):
    values = {
        "detection_status": "not_log",
        "requested_preset_id": "compress-only",
        "applied_preset_id": "compress-only",
        "preset_version": None,
        "manifest_sha256": None,
        "lut_sha256": None,
        "transform_kind": "none",
        "color_transform_status": "not_requested",
        "color_transform_error_code": None,
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    "values",
    [
        _claim(),
        _claim(detection_status="unknown"),
        _claim(
            detection_status="apple_log",
            requested_preset_id="generated-apple-log-rec709",
            color_transform_status="unavailable",
            color_transform_error_code="lut_preset_unavailable",
        ),
        _claim(
            detection_status="apple_log",
            requested_preset_id="generated-apple-log-rec709",
            applied_preset_id="generated-apple-log-rec709",
            preset_version="1.0.0",
            manifest_sha256="a" * 64,
            lut_sha256="b" * 64,
            transform_kind="lut",
            color_transform_status="applied",
        ),
    ],
)
def test_allowed_formal_transform_claims(values):
    assert has_allowed_formal_transform_claim(values) is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"detection_status": "apple_log"},
        {"applied_preset_id": "identity-v1"},
        {"preset_version": "test"},
        {"manifest_sha256": "a" * 64},
        {"lut_sha256": "b" * 64},
        {"color_transform_error_code": "unexpected"},
        {"transform_kind": "lut", "color_transform_status": "applied"},
    ],
)
def test_mixed_or_managed_formal_transform_claims_are_rejected(overrides):
    assert has_allowed_formal_transform_claim(_claim(**overrides)) is False
