import hashlib
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.jobs import insert_job
from app.services.initial_release_guard import (
    InitialReleaseConfigurationError,
    assert_generated_apple_log_conversion_disabled,
)
from app.services.preset_manifest import manifest_document_with_digest
from app.workers import worker
from app.main import app
from scripts.generate_test_luts import generate_cube_bytes


def _settings(tmp_path):
    return Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
        built_in_preset_root=tmp_path / "built-in-luts",
        user_lut_root=tmp_path / "luts",
    )


def _write_generated_preset(
    settings,
    *,
    enabled,
    preset_id="generated-apple-log-rec709",
):
    root = settings.user_lut_root
    assert root is not None
    candidate = root / preset_id
    candidate.mkdir(parents=True)
    cube = generate_cube_bytes(
        preset_id=preset_id, transform="identity"
    )
    (candidate / "transform.cube").write_bytes(cube)
    manifest = {
        "schema_version": 1,
        "preset_id": preset_id,
        "display_name": "Test future conversion",
        "enabled": enabled,
        "preset_kind": "custom",
        "version": "test",
        "source_reference": "test fixture",
        "terms_reference": "test only",
        "target_color_space": "Rec.709",
        "lut_relative_path": "transform.cube",
        "lut_sha256": hashlib.sha256(cube).hexdigest(),
        "file_format": "cube",
        "grid_size": 17,
    }
    (candidate / "manifest.json").write_bytes(manifest_document_with_digest(manifest))


def _configure_reserved_state(settings, *, preset_id, state):
    settings.built_in_preset_root.mkdir(parents=True, exist_ok=True)
    assert settings.user_lut_root is not None
    settings.user_lut_root.mkdir(parents=True, exist_ok=True)
    if state == "absent":
        return
    if state == "disabled":
        _write_generated_preset(settings, enabled=False, preset_id=preset_id)
        return
    if state == "registered_invalid":
        settings.user_lut_root.joinpath(preset_id).mkdir()
        return
    if state == "valid":
        _write_generated_preset(settings, enabled=True, preset_id=preset_id)
        return
    if state == "reserved_namespace_collision":
        settings.built_in_preset_root.joinpath(preset_id).mkdir()
        return
    raise AssertionError(f"unknown test state: {state}")


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
@pytest.mark.parametrize(
    "preset_id",
    ["generated-apple-log-rec709", "generated-apple-log2-rec709"],
)
def test_initial_release_guard_reserved_preset_state_matrix(
    tmp_path,
    preset_id,
    state,
    allowed,
):
    settings = _settings(tmp_path)
    _configure_reserved_state(settings, preset_id=preset_id, state=state)

    if allowed:
        assert_generated_apple_log_conversion_disabled(settings)
    else:
        with pytest.raises(InitialReleaseConfigurationError) as raised:
            assert_generated_apple_log_conversion_disabled(settings)
        assert raised.value.code == "generated_apple_log_conversion_not_approved"


@pytest.mark.parametrize(
    "preset_id",
    ["generated-apple-log-rec709", "generated-apple-log2-rec709"],
)
def test_worker_rejects_valid_generated_preset_before_claim_or_data_change(
    tmp_path, monkeypatch, preset_id
):
    settings = _settings(tmp_path)
    settings.user_lut_root.mkdir()
    _write_generated_preset(settings, enabled=True, preset_id=preset_id)
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)
        queued = insert_job(
            conn,
            job_type="preview",
            asset_id=None,
            payload_json='{"asset_id":"00000000000000000000000000000000"}',
        )
        conn.commit()
    monkeypatch.setattr(worker, "load_settings", lambda: settings)

    with pytest.raises(InitialReleaseConfigurationError):
        worker.run_once()

    with connect(settings.database_path, 5000) as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (queued["id"],)).fetchone()
    assert job["status"] == "queued"
    assert not settings.media_root.exists()


@pytest.mark.parametrize(
    "preset_id",
    ["generated-apple-log-rec709", "generated-apple-log2-rec709"],
)
def test_api_startup_rejects_valid_reserved_preset_before_serving_routes(
    tmp_path,
    monkeypatch,
    preset_id,
):
    settings = _settings(tmp_path)
    settings.user_lut_root.mkdir()
    _write_generated_preset(settings, enabled=True, preset_id=preset_id)
    monkeypatch.setattr("app.main.load_settings", lambda: settings)

    with pytest.raises(InitialReleaseConfigurationError):
        with TestClient(app):
            pytest.fail("startup must reject before capability or asset routes")


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
@pytest.mark.parametrize(
    "preset_id",
    ["generated-apple-log-rec709", "generated-apple-log2-rec709"],
)
def test_api_startup_reserved_preset_state_matrix(
    tmp_path,
    monkeypatch,
    preset_id,
    state,
    allowed,
):
    settings = _settings(tmp_path)
    _configure_reserved_state(settings, preset_id=preset_id, state=state)
    monkeypatch.setattr("app.main.load_settings", lambda: settings)

    if allowed:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
    else:
        with pytest.raises(InitialReleaseConfigurationError):
            with TestClient(app):
                pytest.fail("startup must fail closed")


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
@pytest.mark.parametrize(
    "preset_id",
    ["generated-apple-log-rec709", "generated-apple-log2-rec709"],
)
def test_worker_reserved_preset_state_matrix(
    tmp_path,
    monkeypatch,
    preset_id,
    state,
    allowed,
):
    settings = _settings(tmp_path)
    _configure_reserved_state(settings, preset_id=preset_id, state=state)
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)
    monkeypatch.setattr(worker, "load_settings", lambda: settings)

    if allowed:
        assert worker.run_once() is False
        assert settings.media_root.is_dir()
    else:
        with pytest.raises(InitialReleaseConfigurationError):
            worker.run_once()
        assert not settings.media_root.exists()
