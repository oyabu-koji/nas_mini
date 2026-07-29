import hashlib
import sqlite3
from pathlib import Path


PHASE2B_MIGRATION_VERSION = "008_apple_log_formal_preview"
PHASE2B_SQL_PATH = Path(__file__).parent / f"{PHASE2B_MIGRATION_VERSION}.sql"
EXPECTED_PREVIOUS_MIGRATION_VERSION = "007_managed_preview_presets"


def schema_sql_sha256() -> str:
    return hashlib.sha256(PHASE2B_SQL_PATH.read_bytes()).hexdigest()


def has_valid_phase2b_schema(conn: sqlite3.Connection) -> bool:
    from app.db.phase_schema_identity import resolve_managed_phase_schema

    return resolve_managed_phase_schema(conn).phase2b_valid
