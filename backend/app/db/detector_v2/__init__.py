"""Successor schema support for Apple Log container-signaling detector v2."""

import hashlib

from app.db.phase2c import PHASE2C_SQL_PATH


DETECTOR_V2_MIGRATION_VERSION = "010_apple_log_container_signaling"
EXPECTED_PREVIOUS_MIGRATION_VERSION = "009_safe_delete_candidate"
EXPECTED_PREVIOUS_SCHEMA_SHA256 = (
    "0655f8bae3267bad74f60b6110084327a48c4cb010b60267288c858bb5822d6e"
)


def predecessor_schema_matches() -> bool:
    return hashlib.sha256(PHASE2C_SQL_PATH.read_bytes()).hexdigest() == (
        EXPECTED_PREVIOUS_SCHEMA_SHA256
    )
