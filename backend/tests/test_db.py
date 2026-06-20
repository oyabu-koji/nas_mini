from app.db.connection import connect
from app.db.migrations import run_migrations


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

    assert {"schema_migrations", "assets", "derived_files", "jobs"}.issubset(tables)


def test_connection_sets_wal_and_busy_timeout(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert journal_mode == "wal"
    assert busy_timeout == 5000
