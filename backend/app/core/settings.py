import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_BUILT_IN_PRESET_ROOT = Path(__file__).parents[2] / "assets/lut/presets"
DEFAULT_DETECTOR_ROOT = Path(__file__).parents[2] / "assets/detectors/apple-log-v1"


class SettingsError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    media_root: Path
    api_token: str
    database_path: Path
    lut_path: Path = Path("/app/assets/lut/rec709.cube")
    user_lut_root: Path | None = None
    built_in_preset_root: Path = DEFAULT_BUILT_IN_PRESET_ROOT
    detector_root: Path = DEFAULT_DETECTOR_ROOT
    ffprobe_binary: str = "ffprobe"
    detector_probe_timeout_ms: int = 15_000
    detector_probe_max_stdout_bytes: int = 1_048_576
    detector_probe_max_stderr_bytes: int = 1_048_576
    detector_evidence_max_bytes: int = 4_096
    preset_manifest_max_bytes: int = 65_536
    preset_lut_max_bytes: int = 16 * 1024 * 1024
    sqlite_busy_timeout_ms: int = 5000
    job_lease_seconds: int = 300
    processed_result_recovery_grace_seconds: int = 300
    upload_session_chunk_size_bytes: int = 8_388_608
    upload_session_max_size_bytes: int = 1_099_511_627_776
    upload_session_active_limit: int = 2
    upload_session_expiry_seconds: int = 604_800
    upload_session_retry_after_seconds: int = 30


def load_settings() -> Settings:
    media_root = _required_path("MEDIA_ROOT")
    api_token = _required_value("API_TOKEN", sensitive=True)
    database_path = _required_path("DATABASE_PATH")

    return Settings(
        media_root=media_root,
        api_token=api_token,
        database_path=database_path,
        lut_path=Path(os.environ.get("LUT_PATH", "/app/assets/lut/rec709.cube")),
        user_lut_root=_optional_path("USER_LUT_ROOT"),
        built_in_preset_root=Path(
            os.environ.get("BUILT_IN_PRESET_ROOT", str(DEFAULT_BUILT_IN_PRESET_ROOT))
        ),
        detector_root=Path(
            os.environ.get("APPLE_LOG_DETECTOR_ROOT", str(DEFAULT_DETECTOR_ROOT))
        ),
        ffprobe_binary=os.environ.get("FFPROBE_BINARY", "ffprobe"),
        detector_probe_timeout_ms=_bounded_positive_int(
            "DETECTOR_PROBE_TIMEOUT_MS", 15_000, maximum=15_000
        ),
        detector_probe_max_stdout_bytes=_bounded_positive_int(
            "DETECTOR_PROBE_MAX_STDOUT_BYTES", 1_048_576, maximum=1_048_576
        ),
        detector_probe_max_stderr_bytes=_bounded_positive_int(
            "DETECTOR_PROBE_MAX_STDERR_BYTES", 1_048_576, maximum=1_048_576
        ),
        detector_evidence_max_bytes=_bounded_positive_int(
            "DETECTOR_EVIDENCE_MAX_BYTES", 4_096, maximum=4_096
        ),
        preset_manifest_max_bytes=_bounded_positive_int(
            "PRESET_MANIFEST_MAX_BYTES", 65_536, maximum=65_536
        ),
        preset_lut_max_bytes=_bounded_positive_int(
            "PRESET_LUT_MAX_BYTES", 16 * 1024 * 1024, maximum=16 * 1024 * 1024
        ),
        sqlite_busy_timeout_ms=_positive_int("SQLITE_BUSY_TIMEOUT_MS", 5000),
        job_lease_seconds=_positive_int("JOB_LEASE_SECONDS", 300),
        processed_result_recovery_grace_seconds=_positive_int(
            "PROCESSED_RESULT_RECOVERY_GRACE_SECONDS", 300
        ),
        upload_session_chunk_size_bytes=_positive_int("UPLOAD_SESSION_CHUNK_SIZE_BYTES", 8_388_608),
        upload_session_max_size_bytes=_positive_int("UPLOAD_SESSION_MAX_SIZE_BYTES", 1_099_511_627_776),
        upload_session_active_limit=_positive_int("UPLOAD_SESSION_ACTIVE_LIMIT", 2),
        upload_session_expiry_seconds=_positive_int("UPLOAD_SESSION_EXPIRY_SECONDS", 604_800),
        upload_session_retry_after_seconds=_positive_int("UPLOAD_SESSION_RETRY_AFTER_SECONDS", 30),
    )


def _required_value(name: str, sensitive: bool = False) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        label = "sensitive value" if sensitive else name
        raise SettingsError(f"Missing required setting: {label}")
    return value


def _required_path(name: str) -> Path:
    return Path(_required_value(name))


def _optional_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else None


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


def _bounded_positive_int(name: str, default: int, *, maximum: int) -> int:
    value = _positive_int(name, default)
    if value > maximum:
        raise SettingsError(f"{name} must be at most {maximum}")
    return value
