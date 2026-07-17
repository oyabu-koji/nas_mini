import pytest

from app.core.settings import SettingsError, load_settings


def test_load_settings_requires_values(monkeypatch):
    monkeypatch.delenv("MEDIA_ROOT", raising=False)
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)

    with pytest.raises(SettingsError) as exc_info:
        load_settings()

    assert "MEDIA_ROOT" in str(exc_info.value)


def test_load_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db.sqlite3"))
    monkeypatch.delenv("SQLITE_BUSY_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("JOB_LEASE_SECONDS", raising=False)

    settings = load_settings()

    assert settings.sqlite_busy_timeout_ms == 5000
    assert settings.job_lease_seconds == 300
    assert settings.upload_session_chunk_size_bytes == 8_388_608
    assert settings.upload_session_max_size_bytes == 1_099_511_627_776
    assert settings.upload_session_active_limit == 2
    assert settings.upload_session_expiry_seconds == 604_800
    assert settings.upload_session_retry_after_seconds == 30
    assert str(settings.lut_path) == "/app/assets/lut/rec709.cube"


def test_load_settings_allows_lut_path_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("LUT_PATH", str(tmp_path / "custom.cube"))

    settings = load_settings()

    assert settings.lut_path == tmp_path / "custom.cube"


def test_load_settings_rejects_invalid_numeric_value(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("SQLITE_BUSY_TIMEOUT_MS", "0")

    with pytest.raises(SettingsError):
        load_settings()


def test_load_settings_allows_upload_session_limits_to_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("UPLOAD_SESSION_CHUNK_SIZE_BYTES", "1024")
    monkeypatch.setenv("UPLOAD_SESSION_MAX_SIZE_BYTES", "2048")
    monkeypatch.setenv("UPLOAD_SESSION_ACTIVE_LIMIT", "3")
    monkeypatch.setenv("UPLOAD_SESSION_EXPIRY_SECONDS", "60")
    monkeypatch.setenv("UPLOAD_SESSION_RETRY_AFTER_SECONDS", "5")

    settings = load_settings()

    assert settings.upload_session_chunk_size_bytes == 1024
    assert settings.upload_session_max_size_bytes == 2048
    assert settings.upload_session_active_limit == 3
    assert settings.upload_session_expiry_seconds == 60
    assert settings.upload_session_retry_after_seconds == 5


def test_settings_error_does_not_include_token_value(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("API_TOKEN", "super-secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("JOB_LEASE_SECONDS", "not-a-number")

    with pytest.raises(SettingsError) as exc_info:
        load_settings()

    assert "super-secret-token" not in str(exc_info.value)
