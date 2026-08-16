from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def has_allowed_formal_transform_claim(values: Mapping[str, Any]) -> bool:
    """Return whether persisted formal-preview transform provenance is authoritative."""
    detection_status = values.get("detection_status")
    source_profile = values.get("source_profile")
    requested_preset_id = values.get("requested_preset_id")
    applied_preset_id = values.get("applied_preset_id")
    transform_kind = values.get("transform_kind")
    transform_status = values.get("color_transform_status")
    transform_error = values.get("color_transform_error_code")
    preset_version = values.get("preset_version")
    manifest_sha256 = values.get("manifest_sha256")
    lut_sha256 = values.get("lut_sha256")

    requested_by_profile = {
        "apple-log-1": "generated-apple-log-rec709",
        "apple-log-2": "generated-apple-log2-rec709",
    }
    apple_log_fallback = (
        detection_status == "apple_log"
        and source_profile in requested_by_profile
        and requested_preset_id == requested_by_profile.get(source_profile)
        and applied_preset_id == "compress-only"
        and transform_kind == "none"
        and transform_status == "unavailable"
        and transform_error == "lut_preset_unavailable"
        and preset_version is None
        and manifest_sha256 is None
        and lut_sha256 is None
    )
    ordinary = (
        detection_status in {"not_log", "unknown"}
        and source_profile is None
        and requested_preset_id == "compress-only"
        and applied_preset_id == "compress-only"
        and transform_kind == "none"
        and transform_status == "not_requested"
        and transform_error is None
        and preset_version is None
        and manifest_sha256 is None
        and lut_sha256 is None
    )
    return bool(apple_log_fallback or ordinary)


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
