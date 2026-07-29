from fastapi.testclient import TestClient

from app.main import app
from app.services.phase2_rollout import Phase2RolloutSnapshot


def configure(monkeypatch, tmp_path, *, with_custom=False):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db.sqlite3"))
    if with_custom:
        root = tmp_path / "luts"
        root.mkdir()
        monkeypatch.setenv("USER_LUT_ROOT", str(root))
    else:
        monkeypatch.delenv("USER_LUT_ROOT", raising=False)


def auth():
    return {"Authorization": "Bearer secret-token"}


def test_capabilities_are_authenticated_and_report_fixed_feature_flags(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, with_custom=True)
    with TestClient(app) as client:
        unauthorized = client.get("/api/v1/capabilities")
        response = client.get("/api/v1/capabilities", headers=auth())

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json() == {
        "api_version": "v1",
        "minimum_client_version": None,
        "formal_preview_schema_version": 1,
        "features": {
            "processed_result_delivery": True,
            "managed_preview_presets": True,
            "custom_lut": True,
            "generated_apple_log_conversion": False,
            "numeric_rendition_progress": False,
            "detector_certified": False,
            "formal_apple_log_preview": False,
            "safe_delete_candidate": False,
        },
    }


def test_preset_catalog_requires_auth_and_always_contains_compress_only(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    with TestClient(app) as client:
        unauthorized = client.get("/api/v1/presets")
        response = client.get("/api/v1/presets", headers=auth())

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["preset_id"] == "compress-only"
    assert {item["preset_id"] for item in items} == {
        "compress-only",
        "identity-v1",
        "test-red-blue-swap-v1",
    }
    forbidden = {"lut_sha256", "manifest_sha256", "lut_relative_path"}
    assert all(not forbidden.intersection(item) for item in items)


def test_capabilities_require_mobile_020_only_when_formal_preview_is_enabled(
    monkeypatch, tmp_path
):
    configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.api.capabilities.resolve_phase2_rollout",
        lambda **_kwargs: Phase2RolloutSnapshot(
            phase2b_schema_enabled=True,
            phase2c_schema_enabled=False,
            minimum_client_version="0.2.0",
            phase2_asset=False,
            detector_certified=True,
            formal_apple_log_preview=True,
            safe_delete_candidate=False,
            runtime_blocked_reason=None,
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/capabilities", headers=auth())

    assert response.status_code == 200
    body = response.json()
    assert body["minimum_client_version"] == "0.2.0"
    assert body["features"]["generated_apple_log_conversion"] is False
    assert body["features"]["formal_apple_log_preview"] is True
