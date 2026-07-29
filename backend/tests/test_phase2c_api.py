from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.db.connection import connect
from app.main import app
from app.services.detector_capability import DetectorCapability
from app.services.phase2c_migration import apply_phase2c_migration
from tests.phase2c_test_support import (
    initialize_phase2b,
    insert_eligible_confirmed_asset,
)


def _settings(monkeypatch, tmp_path):
    settings = Settings(
        media_root=tmp_path / "media",
        api_token="secret-token",
        database_path=tmp_path / "db.sqlite3",
        detector_root=tmp_path / "detector",
    )
    monkeypatch.setenv("MEDIA_ROOT", str(settings.media_root))
    monkeypatch.setenv("API_TOKEN", settings.api_token)
    monkeypatch.setenv("DATABASE_PATH", str(settings.database_path))
    monkeypatch.setenv("DETECTOR_ROOT", str(settings.detector_root))
    return settings


def _runtime(available=True):
    return DetectorCapability(
        mode="phase2b_enabled" if available else "phase2a_compatibility",
        detector_certified=available,
        formal_apple_log_preview=available,
        blocked_reason=None if available else "log_detector_manifest_invalid",
    )


def _auth(version=None):
    headers = {"Authorization": "Bearer secret-token"}
    if version is not None:
        headers["X-MediaVault-Client-Version"] = version
    return headers


def _prepare_phase2c(settings, *, review_status="preview_confirmed", content=None):
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(
            conn,
            review_status=review_status,
            result_bytes=content,
        )
        conn.commit()
    apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )


def test_phase2c_capability_response_and_identity_error(
    monkeypatch,
    tmp_path,
):
    settings = _settings(monkeypatch, tmp_path)
    _prepare_phase2c(settings)
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(True),
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/capabilities", headers=_auth())

    assert response.status_code == 200
    assert response.json()["minimum_client_version"] == "0.3.0"
    assert response.json()["features"]["formal_apple_log_preview"] is True
    assert response.json()["features"]["safe_delete_candidate"] is True

    with connect(settings.database_path, 5000) as conn:
        conn.execute("DROP TRIGGER prevent_completed_upload_chunk_insert")
        conn.commit()
    with TestClient(app) as client:
        invalid = client.get("/api/v1/capabilities", headers=_auth())

    assert invalid.status_code == 503
    assert invalid.json() == {
        "code": "phase2c_migration_schema_identity_mismatch",
        "retryable": False,
    }


def test_phase2c_endpoints_reject_020_but_list_and_detail_remain_readable(
    monkeypatch,
    tmp_path,
):
    settings = _settings(monkeypatch, tmp_path)
    _prepare_phase2c(settings)
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(True),
    )
    result_id = f"{1:032x}"

    with TestClient(app) as client:
        listing = client.get("/assets", headers=_auth())
        detail = client.get("/assets/1", headers=_auth())
        protected = [
            client.get("/assets/1/preview", headers=_auth("0.2.0")),
            client.get(
                f"/assets/1/results/{result_id}",
                headers=_auth("0.2.0"),
            ),
            client.post(
                "/assets/1/preview-confirmation",
                headers=_auth("0.2.0"),
            ),
            client.post("/assets/1/preview-confirmation", headers=_auth()),
            client.post(
                "/assets/1/preview-confirmation",
                headers=_auth("v0.3.0"),
            ),
        ]

    assert listing.status_code == 200
    assert detail.status_code == 200
    assert listing.json()["items"][0]["delete_candidate_status"] == (
        "safe_to_delete_candidate"
    )
    assert detail.json()["delete_candidate_status"] == (
        "safe_to_delete_candidate"
    )
    assert all(response.status_code == 409 for response in protected)
    assert all(
        response.json() == {
            "code": "incompatible_client",
            "retryable": False,
        }
        for response in protected
    )


def test_confirmation_promotes_and_subsequent_reads_return_candidate(
    monkeypatch,
    tmp_path,
):
    settings = _settings(monkeypatch, tmp_path)
    content = b"phase2c-api-preview"
    settings.media_root.joinpath("previews").mkdir(parents=True)
    _prepare_phase2c(
        settings,
        review_status="not_reviewed",
        content=content,
    )
    settings.media_root.joinpath("previews/1.mp4").write_bytes(content)
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(True),
    )

    with TestClient(app) as client:
        confirmation = client.post(
            "/assets/1/preview-confirmation",
            headers=_auth("0.3.0"),
        )
        listing = client.get("/assets", headers=_auth())
        detail = client.get("/assets/1", headers=_auth())

    assert confirmation.status_code == 200
    assert confirmation.json()["delete_candidate_status"] == (
        "safe_to_delete_candidate"
    )
    assert listing.json()["items"][0]["delete_candidate_status"] == (
        "safe_to_delete_candidate"
    )
    assert detail.json()["delete_candidate_status"] == (
        "safe_to_delete_candidate"
    )
    serialized = str(
        {
            "confirmation": confirmation.json(),
            "listing": listing.json(),
            "detail": detail.json(),
        }
    )
    for internal in (
        "candidate_reason",
        "schema_sql",
        "expected_file_sha256",
        "original_path",
        "detector_evidence_json",
    ):
        assert internal not in serialized


def test_api_has_no_candidate_mutation_or_public_job_route():
    paths = set(app.openapi()["paths"])

    assert not any("candidate" in path for path in paths)
    assert not any(path.startswith("/jobs") for path in paths)
