from __future__ import annotations

from app.core.settings import Settings
from app.services.preset_registry import (
    RESERVED_PROFILE_PRESET_PAIRS,
    classify_reserved_preset_with_identity,
)


GENERATED_APPLE_LOG_PRESET_ID = "generated-apple-log-rec709"
GENERATED_APPLE_LOG2_PRESET_ID = "generated-apple-log2-rec709"
GENERATED_APPLE_LOG_PRESET_IDS = tuple(
    preset_id for _profile, preset_id in RESERVED_PROFILE_PRESET_PAIRS
)


class InitialReleaseConfigurationError(RuntimeError):
    code = "generated_apple_log_conversion_not_approved"

    def __init__(self):
        super().__init__(self.code)


def assert_generated_apple_log_conversion_disabled(settings: Settings) -> None:
    for preset_id in GENERATED_APPLE_LOG_PRESET_IDS:
        snapshot = classify_reserved_preset_with_identity(settings, preset_id)
        if snapshot.classification not in {"absent", "disabled"}:
            raise InitialReleaseConfigurationError()
