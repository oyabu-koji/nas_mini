import logging
import time

from app.core.settings import SettingsError, load_settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.jobs import (
    SUPPORTED_JOB_TYPES,
    claim_next_job,
    fail_unsupported_job,
    recover_expired_jobs,
)
from app.services.storage import initialize_storage


POLL_INTERVAL_SECONDS = 2

logger = logging.getLogger(__name__)


def run_once() -> bool:
    settings = load_settings()
    initialize_storage(settings.media_root)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        run_migrations(conn)
        recover_expired_jobs(conn)
        job = claim_next_job(conn, settings.job_lease_seconds, SUPPORTED_JOB_TYPES)
        if job is None:
            return False
        fail_unsupported_job(conn, job)
        return True


def run_forever() -> None:
    while True:
        try:
            processed = run_once()
        except SettingsError:
            logger.exception("Worker configuration error")
            raise
        except Exception:
            logger.exception("Worker loop failed")
            processed = False

        if not processed:
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
