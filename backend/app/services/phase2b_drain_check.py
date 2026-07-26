from app.core.settings import load_settings
from app.db.connection import connect
from app.services.phase2b_drain import phase2b_drain_counts


def main() -> int:
    settings = load_settings()
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        counts = phase2b_drain_counts(conn)
    print("drained" if counts.drained else "pending")
    return 0 if counts.drained else 1


if __name__ == "__main__":
    raise SystemExit(main())
