from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.settings import Settings
from app.db.connection import connect
from app.db.phase2b import PHASE2B_MIGRATION_VERSION
from app.db.phase_schema_identity import (
    PhaseSchemaIdentityError,
    resolve_managed_phase_schema,
)
from app.services.apple_log_detector import read_ffprobe_version
from app.services.bounded_subprocess import BoundedProcessError
from app.services.detector_manifest import (
    DetectorValidationError,
    load_certificate_summary,
    load_detector_manifest,
    load_rule_input,
)
from app.services.preset_registry import classify_preset
from app.services.initial_release_guard import (
    InitialReleaseConfigurationError,
    assert_generated_apple_log_conversion_disabled,
)


CompatibilityMode = Literal["phase2a_compatibility", "phase2b_enabled"]


@dataclass(frozen=True)
class DetectorCapability:
    mode: CompatibilityMode
    detector_certified: bool
    formal_apple_log_preview: bool
    blocked_reason: str | None


def evaluate_detector_capability(settings: Settings) -> DetectorCapability:
    runtime = evaluate_detector_runtime(settings)
    if not runtime.detector_certified:
        return runtime
    try:
        with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
            schema = resolve_managed_phase_schema(conn)
    except PhaseSchemaIdentityError as exc:
        return _blocked(exc.code)
    if not schema.phase2b_valid:
        return DetectorCapability(
            mode="phase2a_compatibility",
            detector_certified=True,
            formal_apple_log_preview=False,
            blocked_reason="phase2b_migration_not_applied",
        )
    return runtime


def evaluate_detector_runtime(settings: Settings) -> DetectorCapability:
    rule_path = settings.detector_root / "detector-rule-input-v1.json"
    manifest_path = settings.detector_root / "manifest.json"
    summary_path = settings.detector_root / "certificate-summary.json"
    if not (rule_path.is_file() and manifest_path.is_file() and summary_path.is_file()):
        return _blocked("log_detector_manifest_invalid")
    try:
        rule_input = load_rule_input(rule_path)
        manifest = load_detector_manifest(manifest_path, rule_input=rule_input)
        load_certificate_summary(summary_path, rule_input=rule_input, manifest=manifest)
        version = read_ffprobe_version(
            ffprobe_binary=settings.ffprobe_binary,
            timeout_ms=settings.detector_probe_timeout_ms,
            max_stdout_bytes=settings.detector_probe_max_stdout_bytes,
            max_stderr_bytes=settings.detector_probe_max_stderr_bytes,
        )
        if version != manifest.ffprobe_version:
            return _blocked("log_detector_version_mismatch")
        if classify_preset(settings, "compress-only").registry_classification != "valid":
            return _blocked("log_detector_manifest_invalid")
        assert_generated_apple_log_conversion_disabled(settings)
    except InitialReleaseConfigurationError:
        return _blocked("generated_apple_log_conversion_not_approved")
    except (DetectorValidationError, BoundedProcessError, OSError):
        return _blocked("log_detector_manifest_invalid")
    return DetectorCapability(
        mode="phase2b_enabled",
        detector_certified=True,
        formal_apple_log_preview=True,
        blocked_reason=None,
    )


def _blocked(reason: str) -> DetectorCapability:
    return DetectorCapability(
        mode="phase2a_compatibility",
        detector_certified=False,
        formal_apple_log_preview=False,
        blocked_reason=reason,
    )
