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


def test_load_settings_rejects_invalid_numeric_value(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("SQLITE_BUSY_TIMEOUT_MS", "0")

    with pytest.raises(SettingsError):
        load_settings()


def test_settings_error_does_not_include_token_value(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("API_TOKEN", "super-secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("JOB_LEASE_SECONDS", "not-a-number")

    with pytest.raises(SettingsError) as exc_info:
        load_settings()

    assert "super-secret-token" not in str(exc_info.value)
