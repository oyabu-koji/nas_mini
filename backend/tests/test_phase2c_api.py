from fastapi.testclient import TestClient
import pytest

from app.core.settings import Settings
from app.db.connection import connect
from app.db.detector_v2.schema import DETECTOR_V2_METADATA_TABLE_SQL
from app.main import app
from app.services.detector_capability import DetectorCapability
from app.services.detector_v2_migration import apply_detector_v2_migration
from app.services.phase2c_migration import apply_phase2c_migration
from tests.phase2c_test_support import (
    initialize_phase2b,
    insert_eligible_confirmed_asset,
)
from tests.test_preset_registry import write_custom


def _settings(monkeypatch, tmp_path):
    built_in_preset_root = tmp_path / "built-in-presets"
    user_lut_root = tmp_path / "user-luts"
    built_in_preset_root.mkdir()
    user_lut_root.mkdir()
    settings = Settings(
        media_root=tmp_path / "media",
        api_token="secret-token",
        database_path=tmp_path / "db.sqlite3",
        detector_root=tmp_path / "detector",
        built_in_preset_root=built_in_preset_root,
        user_lut_root=user_lut_root,
    )
    monkeypatch.setenv("MEDIA_ROOT", str(settings.media_root))
    monkeypatch.setenv("API_TOKEN", settings.api_token)
    monkeypatch.setenv("DATABASE_PATH", str(settings.database_path))
    monkeypatch.setenv("DETECTOR_ROOT", str(settings.detector_root))
    monkeypatch.setenv("BUILT_IN_PRESET_ROOT", str(settings.built_in_preset_root))
    monkeypatch.setenv("USER_LUT_ROOT", str(settings.user_lut_root))
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


def _configure_reserved_state(settings, *, preset_id, state):
    if state == "absent":
        return
    if state == "disabled":
        write_custom(settings.user_lut_root, preset_id, enabled=False)
        return
    if state == "registered_invalid":
        settings.user_lut_root.joinpath(preset_id).mkdir()
        return
    if state == "valid":
        write_custom(settings.user_lut_root, preset_id, enabled=True)
        return
    if state == "reserved_namespace_collision":
        settings.built_in_preset_root.joinpath(preset_id).mkdir()
        return
    raise AssertionError(f"unknown test state: {state}")


def _prepare_phase2c(
    settings,
    *,
    review_status="preview_confirmed",
    content=None,
    detector_v2=True,
):
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
    if detector_v2:
        apply_detector_v2_migration(
            settings=settings,
            mode="apply",
            offline_maintenance_confirmed=True,
            api_stopped_confirmed=True,
            release_040_ready_confirmed=True,
            release_readiness_check=lambda _settings: True,
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
    assert response.json()["minimum_client_version"] == "0.4.0"
    assert response.json()["features"]["formal_apple_log_preview"] is True
    assert response.json()["features"]["safe_delete_candidate"] is True

    with connect(settings.database_path, 5000) as conn:
        conn.execute("DROP TRIGGER prevent_completed_upload_chunk_insert")
        conn.commit()
    with TestClient(app) as client:
        invalid = client.get("/api/v1/capabilities", headers=_auth())

    assert invalid.status_code == 503
    assert invalid.json() == {
        "code": "detector_v2_migration_schema_identity_mismatch",
        "retryable": False,
    }


def test_partial_detector_v2_schema_returns_stable_503(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path)
    _prepare_phase2c(settings, detector_v2=False)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(DETECTOR_V2_METADATA_TABLE_SQL)
        conn.commit()

    with TestClient(app) as client:
        invalid = client.get("/api/v1/capabilities", headers=_auth())

    assert invalid.status_code == 503
    assert invalid.json() == {
        "code": "detector_v2_migration_schema_identity_mismatch",
        "retryable": False,
    }


def test_detector_v2_endpoints_reject_030_but_list_remains_readable(
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
        protected = [
            client.get("/assets/1", headers=_auth("0.3.0")),
            client.get("/assets/1", headers=_auth()),
            client.get("/assets/1/preview", headers=_auth("0.3.0")),
            client.get(
                f"/assets/1/results/{result_id}",
                headers=_auth("0.3.0"),
            ),
            client.post(
                "/assets/1/preview-confirmation",
                headers=_auth("0.3.0"),
            ),
            client.post("/assets/1/preview-confirmation", headers=_auth()),
            client.post(
                "/assets/1/preview-confirmation",
                headers=_auth("v0.4.0"),
            ),
        ]

    assert listing.status_code == 200
    assert listing.json()["items"][0]["delete_candidate_status"] == (
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


def test_old_client_can_read_upgrade_metadata_list_and_use_upload_paths(
    monkeypatch,
    tmp_path,
):
    settings = _settings(monkeypatch, tmp_path)
    _prepare_phase2c(settings)
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(True),
    )
    old_headers = _auth("0.3.0")

    with TestClient(app) as client:
        capabilities = client.get(
            "/api/v1/capabilities",
            headers=old_headers,
        )
        listing = client.get("/assets", headers=old_headers)
        image_upload = client.post(
            "/assets/upload",
            headers=old_headers,
            data={"type": "image", "filename": "old-client.jpg"},
            files={"file": ("old-client.jpg", b"old-client", "image/jpeg")},
        )
        session_upload = client.post(
            "/upload-sessions",
            headers=old_headers,
            json={
                "client_upload_id": "old-client-session",
                "filename": "old-client.mov",
                "size_bytes": 8,
                "expected_file_sha256": "a" * 64,
                "is_log": False,
            },
        )

    assert capabilities.status_code == 200
    assert capabilities.json()["minimum_client_version"] == "0.4.0"
    assert listing.status_code == 200
    assert "formal_preview" not in listing.json()["items"][0]
    assert "source_profile" not in listing.json()["items"][0]
    assert image_upload.status_code == 201
    assert session_upload.status_code == 201


def test_managed_rendition_api_is_not_version_gated_on_successor_schema(
    monkeypatch,
    tmp_path,
):
    settings = _settings(monkeypatch, tmp_path)
    content = b"managed-rendition-source"
    settings.media_root.joinpath("previews").mkdir(parents=True)
    _prepare_phase2c(settings, content=content)
    settings.media_root.joinpath("previews/1.mp4").write_bytes(content)
    original = b"original"
    original_path = settings.media_root / "originals/1.mov"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(original)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            """
            UPDATE assets
            SET active_processed_result_id = formal_preview_id
            WHERE id = 1
            """
        )
        conn.commit()
    monkeypatch.setattr(
        "app.services.rendition_creation.hash_file_sha256",
        lambda _path: "a" * 64,
    )
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(True),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assets/1/renditions",
            headers=_auth("0.3.0"),
            json={
                "client_rendition_request_id": "a" * 32,
                "preset_id": "compress-only",
            },
        )

    assert response.status_code == 202
    assert response.json()["requested_preset_id"] == "compress-only"


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
            headers=_auth("0.4.0"),
        )
        listing = client.get("/assets", headers=_auth())
        detail = client.get("/assets/1", headers=_auth("0.4.0"))

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


def test_future_apple_log_applied_claim_is_rejected_by_all_phase2_endpoints(
    monkeypatch,
    tmp_path,
):
    settings = _settings(monkeypatch, tmp_path)
    content = b"future-applied-preview"
    settings.media_root.joinpath("previews").mkdir(parents=True)
    _prepare_phase2c(settings, content=content)
    settings.media_root.joinpath("previews/1.mp4").write_bytes(content)
    with connect(settings.database_path, 5000) as conn:
        trigger_names = (
            "prevent_terminal_formal_preview_attempt_update",
            "prevent_preview_provenance_update",
            "validate_formal_preview_ready",
        )
        trigger_sql = {
            name: conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                (name,),
            ).fetchone()[0]
            for name in trigger_names
        }
        for name in trigger_names:
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute(
            "UPDATE assets SET delete_candidate_status = 'not_candidate' WHERE id = 1"
        )
        conn.execute(
            """
            UPDATE formal_preview_attempts
            SET detection_status = 'apple_log',
                source_profile = 'apple-log-1',
                requested_preset_id = 'generated-apple-log-rec709',
                registry_classification = 'valid',
                applied_preset_id = 'generated-apple-log-rec709',
                preset_display_name = 'Future transform',
                preset_kind = 'lut', preset_version = 'future-1',
                manifest_sha256 = ?, expected_lut_sha256 = ?,
                transform_kind = 'lut', color_transform_status = 'applied',
                color_transform_error_code = NULL
            WHERE asset_id = 1
            """,
            ("f" * 64, "1" * 64),
        )
        conn.execute(
            """
            UPDATE preview_provenance
            SET detection_status = 'apple_log',
                source_profile = 'apple-log-1',
                requested_preset_id = 'generated-apple-log-rec709',
                applied_preset_id = 'generated-apple-log-rec709',
                preset_display_name = 'Future transform',
                preset_kind = 'lut', preset_version = 'future-1',
                manifest_sha256 = ?, lut_sha256 = ?,
                transform_kind = 'lut', color_transform_status = 'applied',
                color_transform_error_code = NULL
            WHERE asset_id = 1
            """,
            ("f" * 64, "1" * 64),
        )
        for sql in trigger_sql.values():
            conn.execute(sql)
        conn.execute(
            """
            UPDATE assets
            SET log_detection_status = 'apple_log', source_profile = 'apple-log-1'
            WHERE id = 1
            """
        )
        conn.commit()

    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(True),
    )
    result_id = f"{1:032x}"
    with TestClient(app) as client:
        responses = [
            client.get("/assets/1", headers=_auth("0.4.0")),
            client.get("/assets/1/preview", headers=_auth("0.4.0")),
            client.get(
                f"/assets/1/results/{result_id}",
                headers=_auth("0.4.0"),
            ),
            client.post(
                "/assets/1/preview-confirmation",
                headers=_auth("0.4.0"),
            ),
        ]

    assert all(response.status_code == 409 for response in responses)
    assert all(
        response.json() == {
            "code": "formal_preview_provenance_invalid",
            "retryable": False,
        }
        for response in responses
    )


@pytest.mark.parametrize(
    "preset_id",
    ["generated-apple-log-rec709", "generated-apple-log2-rec709"],
)
@pytest.mark.parametrize(
    ("state", "allowed"),
    [
        ("absent", True),
        ("disabled", True),
        ("registered_invalid", False),
        ("valid", False),
        ("reserved_namespace_collision", False),
    ],
)
def test_dynamic_reserved_preset_guard_matrix_for_capabilities_and_phase2_endpoints(
    monkeypatch,
    tmp_path,
    preset_id,
    state,
    allowed,
):
    settings = _settings(monkeypatch, tmp_path)
    content = b"dynamic-reserved-preset-preview"
    settings.media_root.joinpath("previews").mkdir(parents=True)
    _prepare_phase2c(settings, content=content)
    settings.media_root.joinpath("previews/1.mp4").write_bytes(content)
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(True),
    )
    result_id = f"{1:032x}"

    with TestClient(app) as client:
        # Startup succeeded with the reserved preset absent. Simulate a
        # post-start registry mutation so every guarded read path must fail
        # closed instead of relying only on the startup check.
        _configure_reserved_state(settings, preset_id=preset_id, state=state)
        responses = [
            client.get("/api/v1/capabilities", headers=_auth()),
            client.get("/assets/1", headers=_auth("0.4.0")),
            client.get("/assets/1/preview", headers=_auth("0.4.0")),
            client.get(
                f"/assets/1/results/{result_id}",
                headers=_auth("0.4.0"),
            ),
            client.post(
                "/assets/1/preview-confirmation",
                headers=_auth("0.4.0"),
            ),
        ]

    if allowed:
        assert all(response.status_code == 200 for response in responses)
    else:
        assert all(response.status_code == 503 for response in responses)
        assert all(
            response.json() == {
                "code": "generated_apple_log_conversion_not_approved",
                "retryable": False,
            }
            for response in responses
        )


def test_api_has_no_candidate_mutation_or_public_job_route():
    paths = set(app.openapi()["paths"])

    assert not any("candidate" in path for path in paths)
    assert not any(path.startswith("/jobs") for path in paths)
