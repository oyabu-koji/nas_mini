import os
from dataclasses import dataclass
from pathlib import Path


class SettingsError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    media_root: Path
    api_token: str
    database_path: Path
    lut_path: Path = Path("/app/assets/lut/rec709.cube")
    sqlite_busy_timeout_ms: int = 5000
    job_lease_seconds: int = 300


def load_settings() -> Settings:
    media_root = _required_path("MEDIA_ROOT")
    api_token = _required_value("API_TOKEN", sensitive=True)
    database_path = _required_path("DATABASE_PATH")

    return Settings(
        media_root=media_root,
        api_token=api_token,
        database_path=database_path,
        lut_path=Path(os.environ.get("LUT_PATH", "/app/assets/lut/rec709.cube")),
        sqlite_busy_timeout_ms=_positive_int("SQLITE_BUSY_TIMEOUT_MS", 5000),
        job_lease_seconds=_positive_int("JOB_LEASE_SECONDS", 300),
    )


def _required_value(name: str, sensitive: bool = False) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        label = "sensitive value" if sensitive else name
        raise SettingsError(f"Missing required setting: {label}")
    return value


def _required_path(name: str) -> Path:
    return Path(_required_value(name))


def _positive_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value == "":
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise SettingsError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise SettingsError(f"{name} must be a positive integer")
    return value
