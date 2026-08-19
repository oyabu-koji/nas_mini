import sqlite3

import pytest
from app.services.offline_startup_migration import MIGRATION_CONTRACT
from app.services.operator_migration_identity import (
    OperatorMigrationIdentityError,
    read_operator_migration_identity,
)


def _write_markers(database_path, versions):
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.executemany(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            ((version,) for version in versions),
        )


def test_reads_actual_last_committed_prefix_without_mutating_database(tmp_path):
    database_path = tmp_path / "database.sqlite3"
    versions = [version for version, _digest in MIGRATION_CONTRACT[:4]]
    _write_markers(database_path, versions)
    before = database_path.read_bytes()

    identity = read_operator_migration_identity(database_path)

    assert identity.last_committed_version == versions[-1]
    assert identity.migration_count == 4
    assert database_path.read_bytes() == before
    assert not (tmp_path / "database.sqlite3-wal").exists()
    assert not (tmp_path / "database.sqlite3-shm").exists()


def test_rejects_non_prefix_marker_history(tmp_path):
    database_path = tmp_path / "database.sqlite3"
    _write_markers(
        database_path,
        [MIGRATION_CONTRACT[0][0], MIGRATION_CONTRACT[2][0]],
    )

    with pytest.raises(OperatorMigrationIdentityError):
        read_operator_migration_identity(database_path)
