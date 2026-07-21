import sqlite3
from pathlib import Path

import pytest

from app.db.connection import connect
from app.db.migrations import _apply_migration, run_migrations
from app.repositories.assets import insert_asset
from app.repositories.derived_files import insert_derived_file
from app.repositories.jobs import insert_job


def _create_pre_004_database(conn: sqlite3.Connection) -> None:
    migrations_dir = Path(__file__).parents[1] / "app/db/migrations"
    with conn:
        conn.executescript((migrations_dir / "001_initial.sql").read_text(encoding="utf-8"))
        conn.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("INSERT INTO schema_migrations (version) VALUES ('001_initial')")
        conn.executescript(
            (migrations_dir / "002_invalidate_identity_log_previews.sql").read_text(
                encoding="utf-8"
            )
        )
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES ('002_invalidate_identity_log_previews')"
        )
        conn.executescript(
            (migrations_dir / "003_phase2a_resumable_uploads.sql").read_text(
                encoding="utf-8"
            )
        )
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES ('003_phase2a_resumable_uploads')"
        )


def _insert_ready_result(
    conn: sqlite3.Connection,
    *,
    result_id: str,
    asset_id: int,
    derived_file_id: int,
) -> None:
    conn.execute(
        """
        INSERT INTO processed_results (
            id, asset_id, derived_file_id, status, mime_type, size_bytes, sha256
        ) VALUES (?, ?, ?, 'ready', 'video/mp4', 10, ?)
        """,
        (result_id, asset_id, derived_file_id, "a" * 64),
    )


def test_processed_results_migration_preserves_phase2a_data_and_foreign_keys(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        _create_pre_004_database(conn)
        with conn:
            asset = insert_asset(
                conn,
                type="video",
                filename="verified.mov",
                original_path="originals/verified.mov",
                size_bytes=10,
                server_sha256="a" * 64,
                taken_at=None,
                latitude=None,
                longitude=None,
                exif_json=None,
                is_log=False,
            )
            derived = insert_derived_file(
                conn,
                asset_id=asset["id"],
                kind="preview",
                path="previews/verified.mp4",
                mime_type="video/mp4",
                size_bytes=10,
            )
            job = insert_job(
                conn,
                job_type="preview",
                asset_id=asset["id"],
                payload_json="{}",
                dedup_key="preview:verified",
            )
            conn.execute(
                """
                INSERT INTO upload_sessions (
                    id, client_upload_id, type, filename, size_bytes,
                    expected_file_sha256, chunk_size_bytes, original_relative_path,
                    status, last_activity_at, expires_at, finalization_job_id, asset_id
                ) VALUES (?, ?, 'video', ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)
                """,
                (
                    "session-1",
                    "client-1",
                    "verified.mov",
                    10,
                    "a" * 64,
                    10,
                    "originals/verified.mov",
                    "2026-07-18T00:00:00+00:00",
                    "2026-07-25T00:00:00+00:00",
                    job["id"],
                    asset["id"],
                ),
            )
            conn.execute(
                """
                INSERT INTO upload_chunks (
                    session_id, chunk_index, start_offset, end_offset, size_bytes, sha256, status
                ) VALUES ('session-1', 0, 0, 9, 10, ?, 'verified')
                """,
                ("a" * 64,),
            )

        run_migrations(conn)

        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        asset_after = conn.execute("SELECT * FROM assets WHERE id = ?", (asset["id"],)).fetchone()
        derived_after = conn.execute(
            "SELECT * FROM derived_files WHERE id = ?", (derived["id"],)
        ).fetchone()
        session_after = conn.execute(
            "SELECT * FROM upload_sessions WHERE id = 'session-1'"
        ).fetchone()
        chunk_count = conn.execute("SELECT COUNT(*) FROM upload_chunks").fetchone()[0]
        foreign_key_check = conn.execute("PRAGMA foreign_key_check").fetchall()
        child_fk_targets = {
            table: {
                row["table"]
                for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            }
            for table in ("derived_files", "jobs", "upload_sessions")
        }
        asset_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'assets'"
        ).fetchone()["sql"]

    assert "processed_results" in tables
    assert asset_after is not None
    assert asset_after["original_path"] == "originals/verified.mov"
    assert asset_after["active_processed_result_id"] is None
    assert derived_after is not None
    assert session_after is not None
    assert session_after["asset_id"] == asset["id"]
    assert chunk_count == 1
    assert foreign_key_check == []
    assert child_fk_targets == {
        "derived_files": {"assets"},
        "jobs": {"assets"},
        "upload_sessions": {"assets", "jobs"},
    }
    assert "REFERENCES processed_results(id) DEFERRABLE INITIALLY DEFERRED" in asset_sql


def test_processed_results_migration_rolls_back_partial_ddl_and_retries(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        _create_pre_004_database(conn)

        def deny_processed_results_table(
            action: int,
            first_argument: str | None,
            _second_argument: str | None,
            _database_name: str | None,
            _source: str | None,
        ) -> int:
            if action == sqlite3.SQLITE_CREATE_TABLE and first_argument == "processed_results":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(deny_processed_results_table)
        with pytest.raises(sqlite3.DatabaseError):
            run_migrations(conn)
        conn.set_authorizer(None)

        tables_after_failure = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        applied_after_failure = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        asset_columns_after_failure = {
            row["name"] for row in conn.execute("PRAGMA table_info(assets)").fetchall()
        }

        run_migrations(conn)
        applied_after_retry = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

    assert "processed_results" not in tables_after_failure
    assert "assets_pre_processed_result" not in tables_after_failure
    assert "active_processed_result_id" not in asset_columns_after_failure
    assert "004_processed_video_delivery" not in applied_after_failure
    assert "004_processed_video_delivery" in applied_after_retry


def test_processed_results_migration_rolls_back_when_ledger_write_fails(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        _create_pre_004_database(conn)
        conn.execute(
            """
            CREATE TRIGGER reject_processed_result_migration_ledger
            BEFORE INSERT ON schema_migrations
            WHEN NEW.version = '004_processed_video_delivery'
            BEGIN
                SELECT RAISE(ABORT, 'ledger_rejected');
            END;
            """
        )

        with pytest.raises(sqlite3.IntegrityError, match="ledger_rejected"):
            run_migrations(conn)

        tables_after_failure = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        applied_after_failure = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

        conn.execute("DROP TRIGGER reject_processed_result_migration_ledger")
        run_migrations(conn)

    assert "processed_results" not in tables_after_failure
    assert "assets_pre_processed_result" not in tables_after_failure
    assert "004_processed_video_delivery" not in applied_after_failure


def test_derived_file_immutability_follow_up_migrates_004_database(tmp_path):
    database_path = tmp_path / "db.sqlite3"
    migrations_dir = Path(__file__).parents[1] / "app/db/migrations"

    with connect(database_path, 5000) as conn:
        _create_pre_004_database(conn)
        _apply_migration(
            conn,
            version="004_processed_video_delivery",
            sql=(migrations_dir / "004_processed_video_delivery.sql").read_text(
                encoding="utf-8"
            ),
        )
        with conn:
            asset = insert_asset(
                conn,
                type="video",
                filename="upgrade.mov",
                original_path="originals/upgrade.mov",
                size_bytes=10,
                server_sha256="a" * 64,
                taken_at=None,
                latitude=None,
                longitude=None,
                exif_json=None,
                is_log=False,
            )
            derived = insert_derived_file(
                conn,
                asset_id=asset["id"],
                kind="preview",
                path="previews/upgrade.mp4",
                mime_type="video/mp4",
                size_bytes=10,
            )
            _insert_ready_result(
                conn,
                result_id="e" * 32,
                asset_id=asset["id"],
                derived_file_id=derived["id"],
            )

        run_migrations(conn)
        applied_versions = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

        with pytest.raises(
            sqlite3.IntegrityError,
            match="processed_result_derived_file_is_immutable",
        ):
            conn.execute(
                "UPDATE derived_files SET path = ? WHERE id = ?",
                ("previews/replaced.mp4", derived["id"]),
            )

    assert "005_enforce_processed_result_derived_file_immutability" in applied_versions


def test_ready_lifecycle_immutability_follow_up_migrates_005_database(tmp_path):
    database_path = tmp_path / "db.sqlite3"
    migrations_dir = Path(__file__).parents[1] / "app/db/migrations"
    result_id = "8" * 32

    with connect(database_path, 5000) as conn:
        _create_pre_004_database(conn)
        for version in (
            "004_processed_video_delivery",
            "005_enforce_processed_result_derived_file_immutability",
        ):
            _apply_migration(
                conn,
                version=version,
                sql=(migrations_dir / f"{version}.sql").read_text(encoding="utf-8"),
            )

        with conn:
            source_asset = insert_asset(
                conn,
                type="video",
                filename="source.mov",
                original_path="originals/source.mov",
                size_bytes=10,
                server_sha256="8" * 64,
                taken_at=None,
                latitude=None,
                longitude=None,
                exif_json=None,
                is_log=False,
            )
            source_derived = insert_derived_file(
                conn,
                asset_id=source_asset["id"],
                kind="preview",
                path="previews/source.mp4",
                mime_type="video/mp4",
                size_bytes=10,
            )
            replacement_asset = insert_asset(
                conn,
                type="video",
                filename="replacement.mov",
                original_path="originals/replacement.mov",
                size_bytes=10,
                server_sha256="9" * 64,
                taken_at=None,
                latitude=None,
                longitude=None,
                exif_json=None,
                is_log=False,
            )
            replacement_derived = insert_derived_file(
                conn,
                asset_id=replacement_asset["id"],
                kind="preview",
                path="previews/replacement.mp4",
                mime_type="video/mp4",
                size_bytes=10,
            )
            _insert_ready_result(
                conn,
                result_id=result_id,
                asset_id=source_asset["id"],
                derived_file_id=source_derived["id"],
            )

        original = dict(
            conn.execute(
                "SELECT * FROM processed_results WHERE id = ?",
                (result_id,),
            ).fetchone()
        )
        run_migrations(conn)
        applied_versions = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

        tampering_updates = (
            ("id = ?", ("a" * 32,)),
            (
                "asset_id = ?, derived_file_id = ?",
                (replacement_asset["id"], replacement_derived["id"]),
            ),
            ("mime_type = ?", ("video/quicktime",)),
            ("size_bytes = ?", (11,)),
            ("sha256 = ?", ("b" * 64,)),
            ("preview_generation = ?", (1,)),
            ("failure_code = ?", ("tampered",)),
            ("created_at = ?", ("2026-07-21T00:00:00Z",)),
        )
        for assignment, values in tampering_updates:
            with pytest.raises(
                sqlite3.IntegrityError,
                match="processed_result_ready_is_immutable",
            ):
                conn.execute(
                    f"""
                    UPDATE processed_results
                    SET status = 'superseded',
                        superseded_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP,
                        {assignment}
                    WHERE id = ?
                    """,
                    (*values, result_id),
                )

        with conn:
            conn.execute(
                """
                UPDATE processed_results
                SET status = 'superseded',
                    superseded_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (result_id,),
            )
        superseded = dict(
            conn.execute(
                "SELECT * FROM processed_results WHERE id = ?",
                (result_id,),
            ).fetchone()
        )

    assert "006_enforce_processed_result_lifecycle_immutability" in applied_versions
    assert superseded["status"] == "superseded"
    assert superseded["superseded_at"] is not None
    for field in (
        "id",
        "asset_id",
        "derived_file_id",
        "mime_type",
        "size_bytes",
        "sha256",
        "preview_generation",
        "failure_code",
        "created_at",
    ):
        assert superseded[field] == original[field]


def test_processed_result_pointer_and_immutability_triggers(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        with conn:
            first_asset = insert_asset(
                conn,
                type="video",
                filename="first.mov",
                original_path="originals/first.mov",
                size_bytes=10,
                server_sha256="1" * 64,
                taken_at=None,
                latitude=None,
                longitude=None,
                exif_json=None,
                is_log=False,
            )
            second_asset = insert_asset(
                conn,
                type="video",
                filename="second.mov",
                original_path="originals/second.mov",
                size_bytes=10,
                server_sha256="2" * 64,
                taken_at=None,
                latitude=None,
                longitude=None,
                exif_json=None,
                is_log=False,
            )
            first_derived = insert_derived_file(
                conn,
                asset_id=first_asset["id"],
                kind="preview",
                path="previews/first.mp4",
                mime_type="video/mp4",
                size_bytes=10,
            )
            second_derived = insert_derived_file(
                conn,
                asset_id=second_asset["id"],
                kind="preview",
                path="previews/second.mp4",
                mime_type="video/mp4",
                size_bytes=10,
            )
            _insert_ready_result(
                conn,
                result_id="1" * 32,
                asset_id=first_asset["id"],
                derived_file_id=first_derived["id"],
            )
            conn.execute(
                """
                INSERT INTO processed_results (id, asset_id, status, failure_code)
                VALUES (?, ?, 'failed', 'preview_failed')
                """,
                ("f" * 32, first_asset["id"]),
            )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="new_asset_active_processed_result_must_be_null",
        ):
            conn.execute(
                """
                INSERT INTO assets (type, filename, original_path, active_processed_result_id)
                VALUES ('video', 'invalid-new.mov', 'originals/invalid-new.mov', ?)
                """,
                ("1" * 32,),
            )

        with pytest.raises(sqlite3.IntegrityError, match="active_processed_result_invalid"):
            conn.execute(
                "UPDATE assets SET active_processed_result_id = ? WHERE id = ?",
                ("1" * 32, second_asset["id"]),
            )

        with pytest.raises(sqlite3.IntegrityError, match="active_processed_result_invalid"):
            conn.execute(
                "UPDATE assets SET active_processed_result_id = ? WHERE id = ?",
                ("f" * 32, first_asset["id"]),
            )

        with pytest.raises(sqlite3.IntegrityError, match="processed_result_derived_file_mismatch"):
            _insert_ready_result(
                conn,
                result_id="2" * 32,
                asset_id=first_asset["id"],
                derived_file_id=second_derived["id"],
            )

        with pytest.raises(sqlite3.IntegrityError):
            _insert_ready_result(
                conn,
                result_id="3" * 32,
                asset_id=first_asset["id"],
                derived_file_id=first_derived["id"],
            )

        with conn:
            conn.execute(
                "UPDATE assets SET active_processed_result_id = ? WHERE id = ?",
                ("1" * 32, first_asset["id"]),
            )

        with pytest.raises(sqlite3.IntegrityError, match="processed_result_ready_is_immutable"):
            conn.execute(
                "UPDATE processed_results SET sha256 = ? WHERE id = ?",
                ("b" * 64, "1" * 32),
            )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="processed_result_active_cannot_be_superseded",
        ):
            conn.execute(
                "UPDATE processed_results SET status = 'superseded', superseded_at = CURRENT_TIMESTAMP WHERE id = ?",
                ("1" * 32,),
            )

        with pytest.raises(sqlite3.IntegrityError, match="processed_result_delete_not_allowed"):
            conn.execute("DELETE FROM processed_results WHERE id = ?", ("1" * 32,))

        with pytest.raises(
            sqlite3.IntegrityError,
            match="processed_result_derived_file_is_immutable",
        ):
            conn.execute(
                "UPDATE derived_files SET mime_type = 'video/quicktime' WHERE id = ?",
                (first_derived["id"],),
            )

        for statement, parameters in (
            (
                "UPDATE derived_files SET path = ? WHERE id = ?",
                ("previews/replaced.mp4", first_derived["id"]),
            ),
            (
                "UPDATE derived_files SET size_bytes = ? WHERE id = ?",
                (11, first_derived["id"]),
            ),
            (
                "UPDATE derived_files SET created_at = ? WHERE id = ?",
                ("2026-07-21T00:00:00Z", first_derived["id"]),
            ),
        ):
            with pytest.raises(
                sqlite3.IntegrityError,
                match="processed_result_derived_file_is_immutable",
            ):
                conn.execute(statement, parameters)

        with conn:
            conn.execute(
                "UPDATE assets SET active_processed_result_id = NULL WHERE id = ?",
                (first_asset["id"],),
            )

        superseded = conn.execute(
            "SELECT status, superseded_at FROM processed_results WHERE id = ?", ("1" * 32,)
        ).fetchone()
        assert superseded["status"] == "superseded"
        assert superseded["superseded_at"] is not None

        with pytest.raises(
            sqlite3.IntegrityError,
            match="processed_result_derived_file_is_immutable",
        ):
            conn.execute(
                "UPDATE derived_files SET path = ? WHERE id = ?",
                ("previews/superseded-replaced.mp4", first_derived["id"]),
            )

        with pytest.raises(sqlite3.IntegrityError, match="active_processed_result_invalid"):
            conn.execute(
                "UPDATE assets SET active_processed_result_id = ? WHERE id = ?",
                ("1" * 32, first_asset["id"]),
            )

        with pytest.raises(sqlite3.IntegrityError, match="processed_result_superseded_is_immutable"):
            conn.execute(
                "UPDATE processed_results SET status = 'ready' WHERE id = ?", ("1" * 32,)
            )

        with pytest.raises(sqlite3.IntegrityError, match="processed_result_delete_not_allowed"):
            conn.execute("DELETE FROM processed_results WHERE id = ?", ("1" * 32,))

        with conn:
            replacement_asset = insert_asset(
                conn,
                type="video",
                filename="replacement.mov",
                original_path="originals/replacement.mov",
                size_bytes=10,
                server_sha256="4" * 64,
                taken_at=None,
                latitude=None,
                longitude=None,
                exif_json=None,
                is_log=False,
            )
            first_replacement_derived = insert_derived_file(
                conn,
                asset_id=replacement_asset["id"],
                kind="preview",
                path="previews/replacement-one.mp4",
                mime_type="video/mp4",
                size_bytes=10,
            )
            second_replacement_derived = insert_derived_file(
                conn,
                asset_id=replacement_asset["id"],
                kind="preview",
                path="previews/replacement-two.mp4",
                mime_type="video/mp4",
                size_bytes=10,
            )
            _insert_ready_result(
                conn,
                result_id="4" * 32,
                asset_id=replacement_asset["id"],
                derived_file_id=first_replacement_derived["id"],
            )
            _insert_ready_result(
                conn,
                result_id="5" * 32,
                asset_id=replacement_asset["id"],
                derived_file_id=second_replacement_derived["id"],
            )

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE assets SET active_processed_result_id = ? WHERE id = ?",
                ("4" * 32, replacement_asset["id"]),
            )
            conn.execute(
                "UPDATE assets SET active_processed_result_id = ? WHERE id = ?",
                ("5" * 32, replacement_asset["id"]),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        replaced = conn.execute(
            "SELECT status FROM processed_results WHERE id = ?", ("4" * 32,)
        ).fetchone()
        active_replacement = conn.execute(
            "SELECT active_processed_result_id FROM assets WHERE id = ?",
            (replacement_asset["id"],),
        ).fetchone()
        assert replaced["status"] == "superseded"
        assert active_replacement["active_processed_result_id"] == "5" * 32


def test_processed_results_migration_preserves_log_safety_trigger_and_original_index(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        with conn:
            log_asset = insert_asset(
                conn,
                type="video",
                filename="log.mov",
                original_path="originals/log.mov",
                size_bytes=10,
                server_sha256="3" * 64,
                taken_at=None,
                latitude=None,
                longitude=None,
                exif_json=None,
                is_log=True,
            )
            conn.execute(
                "UPDATE assets SET preview_status = 'preview_ready' WHERE id = ?",
                (log_asset["id"],),
            )

        log_after = conn.execute(
            "SELECT preview_status FROM assets WHERE id = ?", (log_asset["id"],)
        ).fetchone()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO assets (type, filename, original_path)
                VALUES ('video', 'duplicate.mov', 'originals/log.mov')
                """
            )

    assert log_after["preview_status"] == "failed"
