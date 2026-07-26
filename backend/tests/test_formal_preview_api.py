import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.assets import FormalPreviewFailedResponse
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
        detail = client.get("/assets/1", headers=_auth())
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


def test_generating_formal_preview_is_visible_without_client_header(
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
        detail = client.get("/assets/1", headers=_auth())
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
