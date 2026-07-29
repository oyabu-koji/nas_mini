import sqlite3

import pytest

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.db.phase2b import PHASE2B_MIGRATION_VERSION
from app.db.phase2c import (
    EXPECTED_ASSETS_TABLE_SQL,
    PHASE2C_MIGRATION_VERSION,
    PHASE2C_TRIGGER_SQL_SHA256,
    assets_table_sql_sha256,
    execute_phase2c_sql,
    schema_sql_sha256,
)
from app.db.phase_schema_identity import (
    PhaseSchemaIdentityError,
    resolve_managed_phase_schema,
)
from app.services.phase2c_migration import apply_phase2c_migration
from app.services.safe_delete_candidate import evaluate_safe_delete_candidate
from tests.phase2c_test_support import (
    initialize_phase2b,
    insert_eligible_confirmed_asset,
)


def _settings(tmp_path):
    return Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
        detector_root=tmp_path / "detector",
    )


def _apply(settings, *, dry_run=False, fault_injector=None):
    return apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        dry_run=dry_run,
        runtime_check=lambda _settings: True,
        fault_injector=fault_injector,
    )


def test_phase2c_migration_backfills_exact_schema_identity_and_repeats(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        conn.commit()

    applied = _apply(settings)
    repeated = _apply(settings)

    assert applied.status == "applied"
    assert applied.promoted == 1
    assert repeated.status == "already_applied"
    with connect(settings.database_path, 5000) as conn:
        state = resolve_managed_phase_schema(conn)
        assets_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'assets'"
        ).fetchone()["sql"]
        metadata = conn.execute(
            "SELECT * FROM phase2c_schema_metadata"
        ).fetchone()
        evaluation = evaluate_safe_delete_candidate(conn, asset_id=1)
        candidate = conn.execute(
            "SELECT delete_candidate_status FROM assets WHERE id = 1"
        ).fetchone()[0]
        trigger_names = {
            row["name"]
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'trigger' AND name IN (
                    %s
                )
                """
                % ",".join("?" for _ in PHASE2C_TRIGGER_SQL_SHA256),
                tuple(PHASE2C_TRIGGER_SQL_SHA256),
            )
        }

    assert state.phase2c_valid is True
    assert state.minimum_client_version == "0.3.0"
    assert assets_sql == EXPECTED_ASSETS_TABLE_SQL
    assert metadata["version"] == PHASE2C_MIGRATION_VERSION
    assert metadata["schema_sql_sha256"] == schema_sql_sha256()
    assert metadata["assets_table_sql_sha256"] == assets_table_sql_sha256()
    assert trigger_names == set(PHASE2C_TRIGGER_SQL_SHA256)
    assert evaluation.eligible is True
    assert candidate == "safe_to_delete_candidate"


def test_phase2c_dry_run_rolls_back_schema_marker_and_candidate(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        conn.commit()

    result = _apply(settings, dry_run=True)

    assert result.status == "dry_run"
    assert result.promoted == 1
    with connect(settings.database_path, 5000) as conn:
        assert resolve_managed_phase_schema(conn).phase2c_present is False
        assert conn.execute(
            "SELECT delete_candidate_status FROM assets WHERE id = 1"
        ).fetchone()[0] == "not_candidate"


def test_phase2c_fault_rolls_back_all_schema_and_data(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)

    def fail(step):
        if step == "after_metadata":
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        _apply(settings, fault_injector=fail)

    with connect(settings.database_path, 5000) as conn:
        assert resolve_managed_phase_schema(conn).phase2c_present is False


def test_phase2c_triggers_reject_unknown_or_direct_safe_and_identity_mutation(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        conn.commit()
    _apply(settings)

    with connect(settings.database_path, 5000) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE assets SET delete_candidate_status = 'unknown' WHERE id = 1"
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="completed_upload_session_is_immutable",
        ):
            conn.execute(
                "UPDATE upload_sessions SET size_bytes = 9 WHERE asset_id = 1"
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="completed_upload_chunk_delete_not_allowed",
        ):
            conn.execute(
                """
                DELETE FROM upload_chunks
                WHERE session_id = (SELECT id FROM upload_sessions WHERE asset_id = 1)
                """
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="current_formal_derived_file_is_immutable",
        ):
            conn.execute(
                """
                UPDATE derived_files SET size_bytes = 17
                WHERE id = (
                    SELECT derived_file_id FROM processed_results
                    WHERE id = (SELECT formal_preview_id FROM assets WHERE id = 1)
                )
                """
            )


def test_phase2c_schema_identity_rejects_tampered_trigger(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    _apply(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute("DROP TRIGGER prevent_completed_upload_chunk_insert")
        conn.commit()
        with pytest.raises(
            PhaseSchemaIdentityError,
            match="phase2c_migration_schema_identity_mismatch",
        ):
            resolve_managed_phase_schema(conn)


@pytest.mark.parametrize(
    "statement",
    [
        " -- comment\nBEGIN;",
        "/* comment */ COMMIT;",
        "\nROLLBACK;",
        "SAVEPOINT phase2c;",
        "RELEASE phase2c;",
        "-- comment\nPRAGMA foreign_keys = OFF;",
    ],
)
def test_phase2c_executor_rejects_transaction_control_and_foreign_keys(statement):
    conn = sqlite3.connect(":memory:")
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(RuntimeError):
        execute_phase2c_sql(conn, sql=statement)
    conn.rollback()


def test_phase2c_executor_allows_trigger_body_begin_and_comments():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE sample (id INTEGER)")
    conn.execute("BEGIN IMMEDIATE")
    count = execute_phase2c_sql(
        conn,
        sql="""
        -- leading comment with BEGIN
        CREATE TRIGGER sample_trigger
        BEFORE INSERT ON sample
        BEGIN
            SELECT RAISE(ABORT, 'BEGIN in a string');
        END;
        """,
    )
    assert count == 1
    assert conn.in_transaction is True
    conn.rollback()


def test_valid_007_only_and_original_candidate_column_are_not_phase_signals(
    tmp_path,
):
    settings = _settings(tmp_path)
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)
        state = resolve_managed_phase_schema(conn)
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(assets)")
        }

    assert "delete_candidate_status" in columns
    assert state.phase2b_present is False
    assert state.phase2c_present is False
    assert state.minimum_client_version is None


@pytest.mark.parametrize(
    "signal",
    [
        "marker",
        "metadata",
        "table",
        "index",
        "asset_column",
        "job_column",
        "trigger",
    ],
)
def test_each_partial_phase2b_signal_fails_closed(tmp_path, signal):
    settings = _settings(tmp_path)
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)
        if signal == "marker":
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (PHASE2B_MIGRATION_VERSION,),
            )
        elif signal == "metadata":
            conn.execute(
                """
                CREATE TABLE phase2b_schema_metadata (
                    version TEXT, schema_sql_sha256 TEXT
                )
                """
            )
        elif signal == "table":
            conn.execute("CREATE TABLE formal_preview_attempts (id TEXT)")
        elif signal == "index":
            conn.execute(
                "CREATE INDEX idx_jobs_preview_generation ON jobs(id)"
            )
        elif signal == "asset_column":
            conn.execute(
                "ALTER TABLE assets ADD COLUMN preview_generation INTEGER"
            )
        elif signal == "job_column":
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN preview_generation INTEGER"
            )
        else:
            conn.execute(
                """
                CREATE TRIGGER validate_phase2b_preview_job_insert
                BEFORE INSERT ON jobs BEGIN SELECT 1; END
                """
            )
        with pytest.raises(
            PhaseSchemaIdentityError,
            match="phase2b_migration_schema_identity_mismatch",
        ):
            resolve_managed_phase_schema(conn)


@pytest.mark.parametrize("signal", ["marker", "metadata", "trigger"])
def test_each_partial_phase2c_signal_fails_closed(tmp_path, signal):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        if signal == "marker":
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (PHASE2C_MIGRATION_VERSION,),
            )
        elif signal == "metadata":
            conn.execute(
                """
                CREATE TABLE phase2c_schema_metadata (
                    version TEXT, schema_sql_sha256 TEXT,
                    assets_table_sql_sha256 TEXT
                )
                """
            )
        else:
            conn.execute(
                """
                CREATE TRIGGER prevent_safe_delete_candidate_asset_insert
                BEFORE INSERT ON assets BEGIN SELECT 1; END
                """
            )
        with pytest.raises(
            PhaseSchemaIdentityError,
            match="phase2c_migration_schema_identity_mismatch",
        ):
            resolve_managed_phase_schema(conn)


def test_phase2c_identity_rejects_extra_metadata_and_reserved_objects(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    _apply(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            """
            INSERT INTO phase2c_schema_metadata (
                version, schema_sql_sha256, assets_table_sql_sha256
            ) VALUES ('future', ?, ?)
            """,
            ("a" * 64, "b" * 64),
        )
        with pytest.raises(
            PhaseSchemaIdentityError,
            match="phase2c_migration_schema_identity_mismatch",
        ):
            resolve_managed_phase_schema(conn)
        conn.execute(
            "DELETE FROM phase2c_schema_metadata WHERE version = 'future'"
        )
        conn.execute("CREATE TABLE phase2c_unexpected (id INTEGER)")
        with pytest.raises(
            PhaseSchemaIdentityError,
            match="phase2c_migration_schema_identity_mismatch",
        ):
            resolve_managed_phase_schema(conn)


def test_phase2b_identity_rejects_unexpected_reserved_object(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute("CREATE TABLE phase2b_unexpected (id INTEGER)")
        with pytest.raises(
            PhaseSchemaIdentityError,
            match="phase2b_migration_schema_identity_mismatch",
        ):
            resolve_managed_phase_schema(conn)


def test_phase2c_identity_ignores_unreserved_future_objects(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    _apply(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute("CREATE TABLE future_feature (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE INDEX idx_future_feature ON future_feature(id)")
        conn.execute(
            """
            CREATE TRIGGER future_feature_insert
            BEFORE INSERT ON future_feature BEGIN SELECT 1; END
            """
        )
        state = resolve_managed_phase_schema(conn)

    assert state.phase2c_valid is True


def test_phase2c_presence_validates_phase2b_identity_first(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    _apply(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute("DROP TRIGGER validate_formal_preview_ready")
        conn.execute("DROP TRIGGER prevent_completed_upload_chunk_insert")
        with pytest.raises(
            PhaseSchemaIdentityError,
            match="phase2b_migration_schema_identity_mismatch",
        ):
            resolve_managed_phase_schema(conn)


@pytest.mark.parametrize("tamper", ["whitespace", "quoting", "constraint"])
def test_phase2c_assets_sql_exact_identity_rejects_text_tamper(tmp_path, tamper):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    _apply(settings)
    with connect(settings.database_path, 5000) as conn:
        sql = EXPECTED_ASSETS_TABLE_SQL
        if tamper == "whitespace":
            sql = f"{sql} "
        elif tamper == "quoting":
            sql = sql.replace('CREATE TABLE "assets"', "CREATE TABLE assets", 1)
        else:
            sql = sql.replace(
                "ck_assets_delete_candidate_status",
                "ck_assets_delete_candidate_state",
                1,
            )
        conn.execute("PRAGMA writable_schema = ON")
        conn.execute(
            """
            UPDATE sqlite_master SET sql = ?
            WHERE type = 'table' AND name = 'assets'
            """,
            (sql,),
        )
        conn.execute("PRAGMA writable_schema = OFF")
        with pytest.raises(
            PhaseSchemaIdentityError,
            match="phase2c_migration_schema_identity_mismatch",
        ):
            resolve_managed_phase_schema(conn)


def test_phase2c_migration_preserves_phase2b_assets_structure_and_sequence(
    tmp_path,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn, asset_id=5)
        conn.commit()
        before_columns = [
            tuple(row) for row in conn.execute("PRAGMA table_info(assets)")
        ]
        before_foreign_keys = [
            tuple(row) for row in conn.execute("PRAGMA foreign_key_list(assets)")
        ]
        before_index = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'index' AND name = 'idx_assets_original_path'
            """
        ).fetchone()[0]
        before_triggers = {
            row["name"]: row["sql"]
            for row in conn.execute(
                """
                SELECT name, sql FROM sqlite_master
                WHERE type = 'trigger' AND tbl_name = 'assets'
                """
            )
        }
        before_sequence = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'assets'"
        ).fetchone()[0]

    _apply(settings)

    with connect(settings.database_path, 5000) as conn:
        after_columns = [
            tuple(row) for row in conn.execute("PRAGMA table_info(assets)")
        ]
        after_foreign_keys = [
            tuple(row) for row in conn.execute("PRAGMA foreign_key_list(assets)")
        ]
        after_index = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'index' AND name = 'idx_assets_original_path'
            """
        ).fetchone()[0]
        after_triggers = {
            row["name"]: row["sql"]
            for row in conn.execute(
                """
                SELECT name, sql FROM sqlite_master
                WHERE type = 'trigger' AND tbl_name = 'assets'
                  AND name NOT IN (
                      'prevent_safe_delete_candidate_asset_insert',
                      'enforce_safe_delete_candidate_asset_update',
                      'prevent_finalized_session_asset_update',
                      'prevent_finalized_session_asset_delete'
                  )
                """
            )
        }
        after_sequence = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'assets'"
        ).fetchone()[0]

    assert after_columns == before_columns
    assert after_foreign_keys == before_foreign_keys
    assert after_index == before_index
    assert after_triggers == before_triggers
    assert after_sequence == before_sequence == 5


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE upload_sessions SET type = 'image' WHERE asset_id = 1",
        "UPDATE upload_sessions SET size_bytes = 9 WHERE asset_id = 1",
        f"UPDATE upload_sessions SET expected_file_sha256 = '{'f' * 64}' WHERE asset_id = 1",
        "UPDATE upload_sessions SET chunk_size_bytes = 4 WHERE asset_id = 1",
        "UPDATE upload_sessions SET original_relative_path = 'other.mov' WHERE asset_id = 1",
        "UPDATE upload_sessions SET asset_id = NULL WHERE asset_id = 1",
        "UPDATE upload_sessions SET status = 'failed' WHERE asset_id = 1",
        "DELETE FROM upload_sessions WHERE asset_id = 1",
        "INSERT INTO upload_chunks (session_id, chunk_index, start_offset, end_offset, size_bytes, sha256, status) VALUES ('session-00000001', 1, 8, 15, 8, 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'verified')",
        "UPDATE upload_chunks SET size_bytes = 7 WHERE session_id = 'session-00000001'",
        "DELETE FROM upload_chunks WHERE session_id = 'session-00000001'",
        "UPDATE assets SET type = 'image' WHERE id = 1",
        "UPDATE assets SET original_path = 'other.mov' WHERE id = 1",
        "UPDATE assets SET size_bytes = 9 WHERE id = 1",
        f"UPDATE assets SET server_sha256 = '{'f' * 64}' WHERE id = 1",
        "UPDATE assets SET verification_status = 'server_hash_recorded' WHERE id = 1",
        "DELETE FROM assets WHERE id = 1",
        "UPDATE derived_files SET asset_id = 999 WHERE id = 1",
        "UPDATE derived_files SET kind = 'rendition' WHERE id = 1",
        "UPDATE derived_files SET path = 'other.mp4' WHERE id = 1",
        "UPDATE derived_files SET mime_type = 'video/quicktime' WHERE id = 1",
        "UPDATE derived_files SET size_bytes = 17 WHERE id = 1",
        "DELETE FROM derived_files WHERE id = 1",
    ],
)
def test_phase2c_immutable_authority_matrix_rejects_direct_sql(
    tmp_path,
    statement,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        conn.commit()
    _apply(settings)
    with connect(settings.database_path, 5000) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement)


def test_phase2c_rejects_direct_safe_insert(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    _apply(settings)
    with connect(settings.database_path, 5000) as conn:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="safe_delete_candidate_insert_not_allowed",
        ):
            conn.execute(
                """
                INSERT INTO assets (
                    type, filename, delete_candidate_status
                ) VALUES (
                    'video', 'fixture.mov', 'safe_to_delete_candidate'
                )
                """
            )


@pytest.mark.parametrize(
    "authority_change",
    [
        "preview_status = 'preview_generating'",
        "review_status = 'not_reviewed'",
        "formal_preview_id = NULL",
        (
            "log_detection_status = 'not_evaluated', "
            "detector_rule_version = NULL, "
            "detector_manifest_sha256 = NULL, "
            "detector_evidence_sha256 = NULL"
        ),
    ],
)
def test_candidate_authority_change_requires_same_statement_demotion(
    tmp_path,
    authority_change,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        conn.commit()
    _apply(settings)
    with connect(settings.database_path, 5000) as conn:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="safe_delete_candidate_relation_invalid",
        ):
            conn.execute(
                f"UPDATE assets SET {authority_change} WHERE id = 1"
            )
        conn.execute(
            f"""
            UPDATE assets
            SET {authority_change},
                delete_candidate_status = 'not_candidate'
            WHERE id = 1
            """
        )
        assert conn.execute(
            "SELECT delete_candidate_status FROM assets WHERE id = 1"
        ).fetchone()[0] == "not_candidate"


def test_managed_selection_metadata_does_not_change_valid_candidate(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        conn.commit()
    _apply(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            """
            UPDATE assets
            SET rendition_selection_generation =
                rendition_selection_generation + 1
            WHERE id = 1
            """
        )
        assert conn.execute(
            "SELECT delete_candidate_status FROM assets WHERE id = 1"
        ).fetchone()[0] == "safe_to_delete_candidate"
