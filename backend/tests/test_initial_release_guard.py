import hashlib
from dataclasses import replace

import pytest

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
from scripts.generate_test_luts import generate_cube_bytes


def _settings(tmp_path):
    return Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
        user_lut_root=tmp_path / "luts",
    )


def _write_generated_preset(settings, *, enabled):
    root = settings.user_lut_root
    assert root is not None
    candidate = root / "generated-apple-log-rec709"
    candidate.mkdir(parents=True)
    cube = generate_cube_bytes(
        preset_id="generated-apple-log-rec709", transform="identity"
    )
    (candidate / "transform.cube").write_bytes(cube)
    manifest = {
        "schema_version": 1,
        "preset_id": "generated-apple-log-rec709",
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


def test_initial_release_guard_allows_absent_or_disabled_and_rejects_valid(tmp_path):
    settings = _settings(tmp_path)
    settings.user_lut_root.mkdir()
    assert_generated_apple_log_conversion_disabled(settings)
    _write_generated_preset(settings, enabled=False)
    assert_generated_apple_log_conversion_disabled(settings)

    enabled = tmp_path / "enabled"
    enabled_settings = replace(settings, user_lut_root=enabled)
    enabled.mkdir()
    _write_generated_preset(enabled_settings, enabled=True)

    with pytest.raises(InitialReleaseConfigurationError) as raised:
        assert_generated_apple_log_conversion_disabled(enabled_settings)
    assert raised.value.code == "generated_apple_log_conversion_not_approved"


def test_worker_rejects_valid_generated_preset_before_claim_or_data_change(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    settings.user_lut_root.mkdir()
    _write_generated_preset(settings, enabled=True)
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
