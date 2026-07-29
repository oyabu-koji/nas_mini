import sqlite3

import pytest

from app.core.settings import (
    MAX_UPLOAD_CHUNKS,
    MAX_UPLOAD_SESSION_SIZE_BYTES,
    Settings,
)
from app.db.connection import connect
from app.db.phase2c import (
    PHASE2C_MIGRATION_VERSION,
    PHASE2C_SQL_PATH,
    execute_phase2c_sql,
    iter_complete_statements,
)
from app.db.phase_schema_identity import resolve_managed_phase_schema
from app.services.phase2c_migration import (
    Phase2CMigrationError,
    apply_phase2c_migration,
)
from tests.phase2c_test_support import (
    initialize_phase2b,
    insert_eligible_confirmed_asset,
)


TRUSTED_STATEMENT_FAULTS = [
    f"after_statement_{index}"
    for index, _statement in enumerate(
        iter_complete_statements(PHASE2C_SQL_PATH.read_text(encoding="utf-8")),
        start=1,
    )
]


def _settings(tmp_path):
    return Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
        detector_root=tmp_path / "detector",
    )


def _apply(settings, **kwargs):
    return apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
        **kwargs,
    )


@pytest.mark.parametrize(
    "fault_step",
    [
        *TRUSTED_STATEMENT_FAULTS,
        "after_schema",
        "after_assets_identity",
        "after_marker",
        "after_metadata",
        "after_backfill",
        "after_integrity",
    ],
)
def test_every_phase2c_fault_boundary_rolls_back_schema_and_candidate(
    tmp_path,
    fault_step,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        conn.commit()

    def fail(step):
        if step == fault_step:
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        _apply(settings, fault_injector=fail)

    with connect(settings.database_path, 5000) as conn:
        state = resolve_managed_phase_schema(conn)
        candidate = conn.execute(
            "SELECT delete_candidate_status FROM assets WHERE id = 1"
        ).fetchone()[0]

    assert state.phase2c_present is False
    assert candidate == "not_candidate"


def test_phase2c_preflight_requires_runtime_without_writing(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)

    with pytest.raises(
        Phase2CMigrationError,
        match="phase2c_migration_phase2b_runtime_unavailable",
    ):
        apply_phase2c_migration(
            settings=settings,
            offline_maintenance_confirmed=True,
            runtime_check=lambda _settings: False,
        )

    with connect(settings.database_path, 5000) as conn:
        assert resolve_managed_phase_schema(conn).phase2c_present is False


@pytest.mark.parametrize(
    ("updates", "error_code"),
    [
        (
            ("size_bytes", MAX_UPLOAD_SESSION_SIZE_BYTES + 1),
            "phase2c_migration_upload_limit_exceeded",
        ),
        (
            ("size_bytes", MAX_UPLOAD_CHUNKS + 1),
            "phase2c_migration_chunk_limit_exceeded",
        ),
    ],
)
def test_phase2c_preflight_rejects_upload_bounds_without_writing(
    tmp_path,
    updates,
    error_code,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        column, value = updates
        conn.execute(
            f"UPDATE upload_sessions SET {column} = ?, chunk_size_bytes = 1 "
            "WHERE asset_id = 1",
            (value,),
        )
        conn.commit()

    with pytest.raises(Phase2CMigrationError, match=error_code):
        _apply(settings)

    with connect(settings.database_path, 5000) as conn:
        assert resolve_managed_phase_schema(conn).phase2c_present is False


def test_phase2c_identity_error_precedes_runtime_and_data_preflight(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    runtime_calls = []
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            (PHASE2C_MIGRATION_VERSION,),
        )
        conn.commit()

    with pytest.raises(
        Phase2CMigrationError,
        match="phase2c_migration_schema_identity_mismatch",
    ):
        apply_phase2c_migration(
            settings=settings,
            offline_maintenance_confirmed=True,
            runtime_check=lambda _settings: runtime_calls.append(True) or False,
        )

    assert runtime_calls == []


def test_phase2c_preflight_requires_exact_latest_predecessor(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES ('010_future')"
        )
        conn.commit()

    with pytest.raises(
        Phase2CMigrationError,
        match="phase2c_migration_precondition_changed",
    ):
        _apply(settings)


def test_phase2c_locked_recheck_rejects_state_change_after_read_preflight(
    tmp_path,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)

    def queue_work(step):
        if step != "after_read_preflight":
            return
        with connect(settings.database_path, 5000) as writer:
            writer.execute(
                """
                INSERT INTO assets (
                    id, type, filename, transfer_status,
                    verification_status, preview_status, preview_generation
                ) VALUES (
                    1, 'video', 'fixture.mov', 'transferred',
                    'file_verified', 'preview_generating', 1
                )
                """
            )
            writer.execute(
                """
                INSERT INTO jobs (
                    job_type, status, asset_id, payload_json,
                    dedup_key, preview_generation
                ) VALUES (
                    'preview', 'queued', 1, '{}', 'queued-after-read', 1
                )
                """
            )
            writer.commit()

    with pytest.raises(
        Phase2CMigrationError,
        match="phase2c_migration_preview_not_drained",
    ):
        _apply(settings, fault_injector=queue_work)

    with connect(settings.database_path, 5000) as conn:
        assert resolve_managed_phase_schema(conn).phase2c_present is False


class _GuardedConnection(sqlite3.Connection):
    executescript_calls = 0
    lose_transaction = False

    def execute(self, sql, parameters=(), /):
        cursor = super().execute(sql, parameters)
        if self.lose_transaction and sql.lstrip().startswith("CREATE TABLE"):
            self.commit()
        return cursor

    def executescript(self, sql_script, /):
        self.executescript_calls += 1
        raise AssertionError("executescript must not be called")


def test_phase2c_executor_never_uses_executescript():
    conn = sqlite3.connect(":memory:", factory=_GuardedConnection)
    conn.execute("BEGIN IMMEDIATE")

    assert execute_phase2c_sql(
        conn,
        sql="CREATE TABLE first_table (id INTEGER);",
    ) == 1
    assert conn.executescript_calls == 0
    conn.rollback()


def test_phase2c_executor_detects_lost_transaction():
    conn = sqlite3.connect(":memory:", factory=_GuardedConnection)
    conn.lose_transaction = True
    conn.execute("BEGIN IMMEDIATE")

    with pytest.raises(
        RuntimeError,
        match="phase2c_migration_transaction_lost",
    ):
        execute_phase2c_sql(
            conn,
                sql=(
                    "CREATE TABLE first_table (id INTEGER);\n"
                    "CREATE TABLE second_table (id INTEGER);"
                ),
        )
