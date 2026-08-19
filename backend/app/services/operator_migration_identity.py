from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.db.connection import connect_read_only
from app.db.detector_v2 import DETECTOR_V2_MIGRATION_VERSION
from app.db.phase2b import PHASE2B_MIGRATION_VERSION
from app.db.phase2c import PHASE2C_MIGRATION_VERSION
from app.services.offline_startup_migration import MIGRATION_CONTRACT

EXPECTED_VERSIONS = tuple(version for version, _digest in MIGRATION_CONTRACT) + (
    PHASE2B_MIGRATION_VERSION,
    PHASE2C_MIGRATION_VERSION,
    DETECTOR_V2_MIGRATION_VERSION,
)


class OperatorMigrationIdentityError(RuntimeError):
    def __init__(self, code: str = "operator_migration_identity_invalid"):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class OperatorMigrationIdentity:
    last_committed_version: str
    migration_count: int


def read_operator_migration_identity(
    database_path: Path, *, busy_timeout_ms: int = 5000
) -> OperatorMigrationIdentity:
    try:
        with connect_read_only(database_path, busy_timeout_ms) as conn:
            rows = conn.execute(
                "SELECT version FROM schema_migrations ORDER BY applied_at, rowid"
            ).fetchall()
            versions = tuple(str(row[0]) for row in rows)
            if (
                not versions
                or versions != EXPECTED_VERSIONS[: len(versions)]
                or conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or conn.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise OperatorMigrationIdentityError()
            return OperatorMigrationIdentity(
                last_committed_version=versions[-1],
                migration_count=len(versions),
            )
    except sqlite3.DatabaseError as exc:
        raise OperatorMigrationIdentityError() from exc
