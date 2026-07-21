import sqlite3
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _apply_migration(conn: sqlite3.Connection, *, version: str, sql: str) -> None:
    """Apply one trusted migration and record it as one SQLite transaction."""
    script = "\n".join(
        (
            "BEGIN IMMEDIATE;",
            sql,
            "INSERT INTO schema_migrations (version) VALUES "
            f"({_sql_string_literal(version)});",
            "COMMIT;",
        )
    )

    try:
        conn.executescript(script)
    except sqlite3.DatabaseError:
        if conn.in_transaction:
            conn.rollback()
        raise


def run_migrations(conn: sqlite3.Connection) -> None:
    with conn:
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
        _apply_migration(conn, version=version, sql=sql)
