from __future__ import annotations

from app.core.settings import Settings
from app.services.preset_registry import classify_preset


GENERATED_APPLE_LOG_PRESET_ID = "generated-apple-log-rec709"


class InitialReleaseConfigurationError(RuntimeError):
    code = "generated_apple_log_conversion_not_approved"

    def __init__(self):
        super().__init__(self.code)


def assert_generated_apple_log_conversion_disabled(settings: Settings) -> None:
    snapshot = classify_preset(settings, GENERATED_APPLE_LOG_PRESET_ID)
    if snapshot.registry_classification == "valid":
        raise InitialReleaseConfigurationError()
