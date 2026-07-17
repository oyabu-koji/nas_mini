from pathlib import Path

from app.db.connection import connect
from app.db.migrations import run_migrations


def test_phase2a_migration_backfills_job_dedup_keys_and_adds_session_tables(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        initial_sql = Path(__file__).parents[1] / "app/db/migrations/001_initial.sql"
        conn.executescript(initial_sql.read_text(encoding="utf-8"))
        conn.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("INSERT INTO schema_migrations (version) VALUES ('001_initial')")
        conn.execute("INSERT INTO jobs (job_type) VALUES ('preview')")
        run_migrations(conn)
        job = conn.execute("SELECT dedup_key FROM jobs WHERE id = 1").fetchone()
        session_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(upload_sessions)").fetchall()
        }
        chunk_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(upload_chunks)").fetchall()
        }

    assert job["dedup_key"] == "legacy:1"
    assert {"client_upload_id", "expected_file_sha256", "expires_at", "asset_id"} <= session_columns
    assert {"session_id", "chunk_index", "sha256", "status"} <= chunk_columns
