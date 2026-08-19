import json
import sqlite3

import pytest
from app.services.offline_startup_migration import (
    MIGRATION_CONTRACT,
    MIGRATIONS_DIR,
    OfflineStartupMigrationError,
    _apply_one,
    apply_offline_startup_migrations,
)

from scripts import migrate_startup_offline as cli


def _sql(version):
    return (MIGRATIONS_DIR / f"{version}.sql").read_text(encoding="utf-8")


def _initialize_001(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _apply_one(
        conn,
        version=MIGRATION_CONTRACT[0][0],
        sql=_sql(MIGRATION_CONTRACT[0][0]),
        fault_injector=None,
    )
    conn.close()


def _versions(path):
    with sqlite3.connect(path) as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY rowid"
            )
        ]


def test_offline_migration_applies_exact_002_through_007(tmp_path):
    database = tmp_path / "db.sqlite3"
    _initialize_001(database)

    result = apply_offline_startup_migrations(
        database_path=database,
        offline_maintenance_confirmed=True,
    )

    assert result.status == "applied"
    assert result.applied_count == 6
    assert _versions(database) == [version for version, _digest in MIGRATION_CONTRACT]


def test_offline_migration_accepts_exact_already_complete(tmp_path):
    database = tmp_path / "db.sqlite3"
    _initialize_001(database)
    apply_offline_startup_migrations(
        database_path=database,
        offline_maintenance_confirmed=True,
    )

    result = apply_offline_startup_migrations(
        database_path=database,
        offline_maintenance_confirmed=True,
    )

    assert result.status == "already_complete"
    assert result.applied_count == 0


def test_offline_migration_rejects_sql_digest_mismatch_before_mutation(tmp_path):
    database = tmp_path / "db.sqlite3"
    _initialize_001(database)

    def load(version):
        value = _sql(version)
        return value + "\nSELECT 1;" if version.startswith("004_") else value

    with pytest.raises(
        OfflineStartupMigrationError,
        match="offline_migration_sql_identity_mismatch",
    ):
        apply_offline_startup_migrations(
            database_path=database,
            offline_maintenance_confirmed=True,
            sql_loader=load,
        )

    assert _versions(database) == ["001_initial"]


def test_offline_migration_rejects_schema_mismatch(tmp_path):
    database = tmp_path / "db.sqlite3"
    _initialize_001(database)
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE unexpected (id INTEGER)")

    with pytest.raises(
        OfflineStartupMigrationError,
        match="offline_migration_schema_identity_mismatch",
    ):
        apply_offline_startup_migrations(
            database_path=database,
            offline_maintenance_confirmed=True,
        )


@pytest.mark.parametrize("fault_suffix", ["statement_1", "marker"])
def test_first_migration_transaction_failure_does_not_require_restore(
    tmp_path,
    fault_suffix,
):
    database = tmp_path / "db.sqlite3"
    _initialize_001(database)

    def inject(step):
        if step == f"after_002_invalidate_identity_log_previews_{fault_suffix}":
            raise RuntimeError("fault")

    with pytest.raises(OfflineStartupMigrationError) as captured:
        apply_offline_startup_migrations(
            database_path=database,
            offline_maintenance_confirmed=True,
            fault_injector=inject,
        )

    assert captured.value.code == "offline_migration_failed"
    assert captured.value.restore_required is False
    assert _versions(database) == ["001_initial"]


@pytest.mark.parametrize(
    "target", [version for version, _digest in MIGRATION_CONTRACT[1:]]
)
def test_commit_fault_requires_restore_and_never_auto_resumes(tmp_path, target):
    database = tmp_path / "db.sqlite3"
    _initialize_001(database)

    def inject(step):
        if step == f"after_{target}_commit":
            raise RuntimeError("fault")

    with pytest.raises(OfflineStartupMigrationError) as captured:
        apply_offline_startup_migrations(
            database_path=database,
            offline_maintenance_confirmed=True,
            fault_injector=inject,
        )

    assert captured.value.code == "offline_migration_partial_commit_restore_required"
    assert captured.value.restore_required is True
    committed = _versions(database)
    assert committed[-1] == target

    if target == MIGRATION_CONTRACT[-1][0]:
        repeated = apply_offline_startup_migrations(
            database_path=database,
            offline_maintenance_confirmed=True,
        )
        assert repeated.status == "already_complete"
    else:
        with pytest.raises(
            OfflineStartupMigrationError,
            match="offline_migration_partial_commit_restore_required",
        ):
            apply_offline_startup_migrations(
                database_path=database,
                offline_maintenance_confirmed=True,
            )
    assert _versions(database) == committed


def test_cli_outputs_sanitized_result(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_PATH", "/not/output")
    monkeypatch.setattr(
        cli, "require_disposable_database_target", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        cli, "claim_disposable_database_operation", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        cli,
        "apply_offline_startup_migrations",
        lambda **_kwargs: type(
            "Result",
            (),
            {
                "status": "applied",
                "last_committed_version": "007_managed_preview_presets",
                "applied_count": 6,
                "restore_required": False,
            },
        )(),
    )

    assert cli.main(["--apply", "--offline-maintenance-confirmed"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "applied"
    assert "/not/output" not in output
