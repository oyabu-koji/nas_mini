import logging
import time

from app.core.settings import SettingsError, load_settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.db.phase2b import has_valid_phase2b_schema
from app.repositories.jobs import (
    SUPPORTED_JOB_TYPES,
    claim_next_job,
    fail_unsupported_job,
    recover_expired_jobs,
)
from app.services.processed_result_backfill import backfill_eligible_processed_results
from app.services.processed_result_recovery import recover_unreferenced_generated_previews
from app.services.preview import process_preview_job
from app.services.rendition_processing import process_rendition_job
from app.services.formal_preview_processing import process_formal_preview_job
from app.services.upload_finalize import process_upload_finalize_job
from app.services.storage import initialize_storage
from app.services.initial_release_guard import (
    InitialReleaseConfigurationError,
    assert_generated_apple_log_conversion_disabled,
)


POLL_INTERVAL_SECONDS = 2

logger = logging.getLogger(__name__)


def run_once() -> bool:
    settings = load_settings()
    assert_generated_apple_log_conversion_disabled(settings)
    initialize_storage(settings.media_root)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        run_migrations(conn)
        recover_expired_jobs(conn)
        phase2b_enabled = has_valid_phase2b_schema(conn)
    recover_unreferenced_generated_previews(settings=settings)
    backfill_eligible_processed_results(settings=settings)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        job = claim_next_job(conn, settings.job_lease_seconds, SUPPORTED_JOB_TYPES)
        if job is None:
            return False
    if job["job_type"] == "upload_finalize":
        return process_upload_finalize_job(settings=settings, job=job)
    if (
        phase2b_enabled
        and job["job_type"] == "preview"
        and job.get("preview_generation") is not None
    ):
        return process_formal_preview_job(settings=settings, job=job)
    if job["job_type"] in {"preview", "lut_preview"}:
        return process_preview_job(settings=settings, job=job)
    if job["job_type"] == "rendition":
        return process_rendition_job(settings=settings, job=job)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        fail_unsupported_job(conn, job)
    return True


def run_forever() -> None:
    while True:
        try:
            processed = run_once()
        except (SettingsError, InitialReleaseConfigurationError):
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
