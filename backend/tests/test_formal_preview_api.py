import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.assets import (
    FormalPreviewFailedResponse,
    FormalPreviewGeneratingResponse,
    FormalPreviewReadyResponse,
)
from app.services.client_compatibility import parse_semantic_version
from tests.test_formal_preview_processing import (
    _claimed_formal_job,
    _prepare_verified_original,
    _run_formal_success,
    _settings,
)


def _configure(monkeypatch, settings):
    monkeypatch.setenv("MEDIA_ROOT", str(settings.media_root))
    monkeypatch.setenv("API_TOKEN", settings.api_token)
    monkeypatch.setenv("DATABASE_PATH", str(settings.database_path))
    monkeypatch.setenv("APPLE_LOG_DETECTOR_ROOT", str(settings.detector_root))


def _auth(client_version=None):
    headers = {"Authorization": "Bearer test-token"}
    if client_version is not None:
        headers["X-MediaVault-Client-Version"] = client_version
    return headers


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("0.2.0", "0.2.0", 0),
        ("0.10.0", "0.2.0", 1),
        ("1.0.0", "2.0.0", -1),
    ],
)
def test_semantic_version_comparison_is_numeric(left, right, expected):
    left_version = parse_semantic_version(left)
    right_version = parse_semantic_version(right)
    comparison = (left_version > right_version) - (left_version < right_version)
    assert comparison == expected


@pytest.mark.parametrize(
    "value",
    ["", "0.2", "0.2.0.0", "v0.2.0", "00.2.0", "0.02.0", "0.2.-1", "0.2.0+build"],
)
def test_semantic_version_rejects_malformed_values(value):
    with pytest.raises(ValueError):
        parse_semantic_version(value)


def test_ready_formal_preview_detail_and_versioned_actions(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    _configure(monkeypatch, settings)
    job = _claimed_formal_job(
        settings,
        payload={
            "asset_id": 1,
            "preview_generation": 1,
            "detection_required": True,
        },
    )
    _prepare_verified_original(settings)
    _run_formal_success(
        settings, job, status="apple_log", monkeypatch=monkeypatch
    )

    with TestClient(app) as client:
        detail = client.get("/assets/1", headers=_auth("0.2.0"))
        listing = client.get("/assets", headers=_auth())
        missing = client.get("/assets/1/preview", headers=_auth())
        old = client.get("/assets/1/preview", headers=_auth("0.1.9"))
        malformed = client.get("/assets/1/preview", headers=_auth("v0.2.0"))
        preview = client.get("/assets/1/preview", headers=_auth("0.2.0"))
        result_id = detail.json()["formal_preview"]["result"]["result_id"]
        exact = client.get(
            f"/assets/1/results/{result_id}", headers=_auth("0.2.0")
        )
        confirmed = client.post(
            "/assets/1/preview-confirmation", headers=_auth("0.2.0")
        )

    assert detail.status_code == 200
    formal = detail.json()["formal_preview"]
    assert formal["schema_version"] == 1
    assert formal["state"] == "ready"
    assert formal["generation"] == 1
    assert formal["detection_status"] == "apple_log"
    assert formal["requested_preset_id"] == "generated-apple-log-rec709"
    assert formal["applied_preset_id"] == "compress-only"
    assert formal["transform_kind"] == "none"
    assert formal["color_transform_status"] == "unavailable"
    assert formal["color_transform_error_code"] == "lut_preset_unavailable"
    assert formal["failure_code"] is None
    assert "formal_preview" not in listing.json()["items"][0]
    for response in (missing, old, malformed):
        assert response.status_code == 409
        assert response.json()["code"] == "incompatible_client"
    assert preview.status_code == 200
    assert preview.content == b"encoded-preview"
    assert exact.status_code == 200
    assert exact.content == b"encoded-preview"
    assert confirmed.status_code == 200
    assert confirmed.json()["review_status"] == "preview_confirmed"
    serialized = detail.text
    assert "originals/sessions" not in serialized
    assert "detector-rule-input" not in serialized
    assert "classification" not in serialized


def test_generating_formal_preview_is_visible_with_compatible_client_header(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    _configure(monkeypatch, settings)
    _claimed_formal_job(
        settings,
        payload={
            "asset_id": 1,
            "preview_generation": 1,
            "detection_required": True,
        },
    )

    with TestClient(app) as client:
        detail = client.get("/assets/1", headers=_auth("0.2.0"))
        preview = client.get("/assets/1/preview", headers=_auth("0.2.0"))

    assert detail.status_code == 200
    assert detail.json()["formal_preview"] == {
        "schema_version": 1,
        "state": "generating",
        "generation": 1,
        "detection_status": None,
        "source_profile": None,
        "detector_rule_version": None,
        "detector_manifest_sha256": None,
        "detector_evidence_sha256": None,
        "requested_preset_id": None,
        "applied_preset_id": None,
        "applied_preset_display_name": None,
        "preset_version": None,
        "manifest_sha256": None,
        "lut_sha256": None,
        "transform_kind": None,
        "color_transform_status": None,
        "color_transform_error_code": None,
        "preview_id": None,
        "result": None,
        "failure_code": None,
    }
    assert preview.status_code == 409
    assert preview.json()["code"] == "formal_preview_not_ready"


def test_failed_schema_rejects_unknown_failure_code():
    with pytest.raises(ValidationError):
        FormalPreviewFailedResponse(
            state="failed",
            generation=1,
            failure_code="raw exception text",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"transform_kind": "lut"},
        {"applied_preset_id": "generated-apple-log2-rec709"},
        {"manifest_sha256": "c" * 64},
        {
            "applied_preset_id": "generated-apple-log2-rec709",
            "applied_preset_display_name": "Future conversion",
            "preset_version": "future-1",
            "manifest_sha256": "c" * 64,
            "lut_sha256": "d" * 64,
            "transform_kind": "lut",
            "color_transform_status": "applied",
            "color_transform_error_code": None,
        },
    ],
)
def test_ready_schema_rejects_apple_log_lut_or_applied_identity(overrides):
    values = {
        "state": "ready",
        "generation": 1,
        "detection_status": "apple_log",
        "source_profile": "apple-log-2",
        "detector_rule_version": "rule-v2",
        "detector_manifest_sha256": "a" * 64,
        "detector_evidence_sha256": "b" * 64,
        "requested_preset_id": "generated-apple-log2-rec709",
        "applied_preset_id": "compress-only",
        "transform_kind": "none",
        "color_transform_status": "unavailable",
        "color_transform_error_code": "lut_preset_unavailable",
        "preview_id": "e" * 32,
        "result": {
            "result_id": "f" * 32,
            "mime_type": "video/mp4",
            "size_bytes": 1,
            "sha256": "0" * 64,
            "created_at": "2026-08-14T00:00:00Z",
            "url": "/assets/1/results/" + "f" * 32,
        },
    }
    values.update(overrides)

    with pytest.raises(ValidationError):
        FormalPreviewReadyResponse(**values)


@pytest.mark.parametrize(
    "source_profile",
    ["apple-log-3", "Apple Log 2", ""],
)
def test_partial_schema_rejects_unknown_source_profile(source_profile):
    with pytest.raises(ValidationError):
        FormalPreviewGeneratingResponse(
            state="generating",
            generation=1,
            detection_status="apple_log",
            source_profile=source_profile,
            detector_rule_version="rule-v2",
            detector_manifest_sha256="a" * 64,
            detector_evidence_sha256="b" * 64,
        )


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (
            FormalPreviewGeneratingResponse,
            {
                "state": "generating",
                "generation": 1,
                "detection_status": "apple_log",
                "source_profile": "apple-log-1",
                "detector_rule_version": "rule-v2",
                "detector_manifest_sha256": "a" * 64,
                "detector_evidence_sha256": "b" * 64,
                "requested_preset_id": "generated-apple-log2-rec709",
            },
        ),
        (
            FormalPreviewGeneratingResponse,
            {
                "state": "generating",
                "generation": 1,
                "detection_status": "apple_log",
                "source_profile": "apple-log-2",
                "detector_rule_version": "rule-v2",
                "detector_manifest_sha256": "a" * 64,
                "detector_evidence_sha256": "b" * 64,
                "requested_preset_id": "generated-apple-log2-rec709",
                "applied_preset_id": "generated-apple-log2-rec709",
                "preset_version": "future",
                "manifest_sha256": "c" * 64,
                "lut_sha256": "d" * 64,
                "transform_kind": "lut",
                "color_transform_status": "applied",
            },
        ),
        (
            FormalPreviewFailedResponse,
            {
                "state": "failed",
                "generation": 1,
                "detection_status": "apple_log",
                "source_profile": "apple-log-1",
                "detector_rule_version": "rule-v2",
                "detector_manifest_sha256": "a" * 64,
                "detector_evidence_sha256": "b" * 64,
                "requested_preset_id": "generated-apple-log2-rec709",
                "transform_kind": "none",
                "color_transform_status": "failed",
                "failure_code": "formal_preview_render_failed",
            },
        ),
    ],
)
def test_partial_formal_preview_schema_rejects_invalid_profile_claim(model, values):
    with pytest.raises(ValidationError):
        model(**values)
