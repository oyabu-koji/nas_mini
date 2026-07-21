import sqlite3
from pathlib import Path

import pytest

import app.db.migrations as migrations
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.assets import get_asset, insert_asset
from app.repositories.derived_files import insert_derived_file
from app.repositories.jobs import insert_job


def test_run_migrations_creates_expected_tables(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert {
        "schema_migrations",
        "assets",
        "derived_files",
        "jobs",
        "processed_results",
    }.issubset(tables)


def test_run_migrations_rolls_back_failed_migration_schema_and_ledger(tmp_path, monkeypatch):
    database_path = tmp_path / "db.sqlite3"
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "001_base.sql").write_text(
        "CREATE TABLE base_table (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    broken_migration = migration_dir / "002_broken.sql"
    broken_migration.write_text(
        "\n".join(
            (
                "CREATE TABLE partial_table (id INTEGER PRIMARY KEY);",
                "SELECT missing_column FROM missing_table;",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", migration_dir)

    with connect(database_path, 5000) as conn:
        with pytest.raises(sqlite3.OperationalError):
            migrations.run_migrations(conn)

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

        broken_migration.write_text(
            "CREATE TABLE recovered_table (id INTEGER PRIMARY KEY);\n",
            encoding="utf-8",
        )
        migrations.run_migrations(conn)
        applied_after_retry = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

    assert "base_table" in tables_after_failure
    assert "partial_table" not in tables_after_failure
    assert applied_after_failure == {"001_base"}
    assert applied_after_retry == {"001_base", "002_broken"}


def test_connection_sets_wal_and_busy_timeout(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert journal_mode == "wal"
    assert busy_timeout == 5000


def test_log_preview_invalidation_migration_preserves_files_and_jobs(tmp_path):
    database_path = tmp_path / "db.sqlite3"
    initial_schema = Path(__file__).parents[1] / "app/db/migrations/001_initial.sql"

    with connect(database_path, 5000) as conn:
        with conn:
            conn.executescript(initial_schema.read_text(encoding="utf-8"))
            conn.execute(
                "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT)"
            )
            conn.execute("INSERT INTO schema_migrations (version) VALUES ('001_initial')")
            log_asset = insert_asset(
                conn,
                type="video",
                filename="log.mov",
                original_path="originals/log.mov",
                size_bytes=10,
                server_sha256="log",
                taken_at=None,
                latitude=None,
                longitude=None,
                exif_json=None,
                is_log=True,
            )
            regular_asset = insert_asset(
                conn,
                type="video",
                filename="regular.mov",
                original_path="originals/regular.mov",
                size_bytes=10,
                server_sha256="regular",
                taken_at=None,
                latitude=None,
                longitude=None,
                exif_json=None,
                is_log=False,
            )
            conn.execute(
                "UPDATE assets SET preview_status = 'preview_ready', review_status = 'preview_confirmed' WHERE id = ?",
                (log_asset["id"],),
            )
            conn.execute(
                "UPDATE assets SET preview_status = 'preview_ready' WHERE id = ?",
                (regular_asset["id"],),
            )
            insert_derived_file(
                conn,
                asset_id=log_asset["id"],
                kind="preview",
                path="previews/log.mp4",
                mime_type="video/mp4",
                size_bytes=10,
            )
            job = insert_job(
                conn,
                job_type="lut_preview",
                asset_id=log_asset["id"],
                payload_json="{}",
            )
            conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (job["id"],))

        run_migrations(conn)
        invalidated = get_asset(conn, log_asset["id"])
        unchanged = get_asset(conn, regular_asset["id"])
        derived_count = conn.execute("SELECT COUNT(*) FROM derived_files").fetchone()[0]
        job_row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        run_migrations(conn)
        rerun = get_asset(conn, log_asset["id"])

    assert invalidated is not None
    assert invalidated["preview_status"] == "failed"
    assert invalidated["review_status"] == "not_reviewed"
    assert unchanged is not None
    assert unchanged["preview_status"] == "preview_ready"
    assert derived_count == 1
    assert job_row["status"] == "done"
    assert rerun is not None
    assert rerun["preview_status"] == "failed"


def test_log_preview_trigger_rejects_old_worker_ready_update(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        asset = insert_asset(
            conn,
            type="video",
            filename="log.mov",
            original_path="originals/log.mov",
            size_bytes=10,
            server_sha256="log",
            taken_at=None,
            latitude=None,
            longitude=None,
            exif_json=None,
            is_log=True,
        )
        conn.execute(
            "UPDATE assets SET preview_status = 'preview_ready', review_status = 'preview_confirmed' WHERE id = ?",
            (asset["id"],),
        )
        updated = get_asset(conn, asset["id"])

    assert updated is not None
    assert updated["preview_status"] == "failed"
    assert updated["review_status"] == "not_reviewed"
