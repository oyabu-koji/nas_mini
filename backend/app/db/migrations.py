import sqlite3
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    applied_versions = {
        row["version"]
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }

    for migration_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = migration_path.stem
        if version in applied_versions:
            continue
        sql = migration_path.read_text(encoding="utf-8")
        with conn:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )
