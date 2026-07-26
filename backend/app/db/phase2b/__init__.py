import hashlib
import sqlite3
from pathlib import Path


PHASE2B_MIGRATION_VERSION = "008_apple_log_formal_preview"
PHASE2B_SQL_PATH = Path(__file__).parent / f"{PHASE2B_MIGRATION_VERSION}.sql"
EXPECTED_PREVIOUS_MIGRATION_VERSION = "007_managed_preview_presets"


def schema_sql_sha256() -> str:
    return hashlib.sha256(PHASE2B_SQL_PATH.read_bytes()).hexdigest()


def has_valid_phase2b_schema(conn: sqlite3.Connection) -> bool:
    marker = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?",
        (PHASE2B_MIGRATION_VERSION,),
    ).fetchone()
    if marker is None:
        return False
    try:
        identity = conn.execute(
            """
            SELECT schema_sql_sha256
            FROM phase2b_schema_metadata
            WHERE version = ?
            """,
            (PHASE2B_MIGRATION_VERSION,),
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError("phase2b_migration_schema_identity_mismatch") from exc
    if identity is None or identity["schema_sql_sha256"] != schema_sql_sha256():
        raise RuntimeError("phase2b_migration_schema_identity_mismatch")
    return True
