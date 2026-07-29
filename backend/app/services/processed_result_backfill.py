import sqlite3
from dataclasses import dataclass
from typing import Literal

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.processed_results import (
    clear_active_processed_result,
    get_active_processed_result,
    get_phase2a_backfill_candidate,
    insert_ready_processed_result,
    list_phase2a_backfill_candidates,
    set_active_processed_result,
)
from app.services.processed_result_integrity import (
    ProcessedResultIntegrityError,
    inspect_derived_preview,
)


BackfillStatus = Literal[
    "created",
    "already_active",
    "integrity_failed",
    "retryable_failure",
]

BACKFILL_INTEGRITY_FAILURE_CODE = "processed_result_backfill_integrity_failed"
BACKFILL_RETRYABLE_FAILURE_CODE = "processed_result_backfill_retryable_failure"


@dataclass(frozen=True)
class ProcessedResultBackfillOutcome:
    asset_id: int
    status: BackfillStatus
    error_code: str | None = None


def backfill_eligible_processed_results(
    *,
    settings: Settings,
) -> list[ProcessedResultBackfillOutcome]:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        asset_ids = [asset["id"] for asset, _derived_file in list_phase2a_backfill_candidates(conn)]

    outcomes: list[ProcessedResultBackfillOutcome] = []
    for asset_id in asset_ids:
        outcome = backfill_processed_result_for_asset(settings=settings, asset_id=asset_id)
        if outcome is not None:
            persist_backfill_outcome(settings=settings, outcome=outcome)
            outcomes.append(outcome)
    return outcomes


def backfill_processed_result_for_asset(
    *,
    settings: Settings,
    asset_id: int,
) -> ProcessedResultBackfillOutcome | None:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        candidate = get_phase2a_backfill_candidate(conn, asset_id=asset_id)
    if candidate is None:
        return None

    _asset, derived_file = candidate
    try:
        inspected = inspect_derived_preview(settings=settings, derived_file=derived_file)
    except ProcessedResultIntegrityError as exc:
        return ProcessedResultBackfillOutcome(
            asset_id=asset_id,
            status=("retryable_failure" if exc.code == "processed_result_file_unavailable" else "integrity_failed"),
            error_code=(
                BACKFILL_RETRYABLE_FAILURE_CODE
                if exc.code == "processed_result_file_unavailable"
                else BACKFILL_INTEGRITY_FAILURE_CODE
            ),
        )

    try:
        with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current_candidate = get_phase2a_backfill_candidate(conn, asset_id=asset_id)
                if current_candidate is None:
                    conn.commit()
                    return None
                asset, current_derived_file = current_candidate
                if current_derived_file["id"] != derived_file["id"]:
                    conn.commit()
                    return ProcessedResultBackfillOutcome(
                        asset_id=asset_id,
                        status="retryable_failure",
                        error_code=BACKFILL_RETRYABLE_FAILURE_CODE,
                    )

                active = get_active_processed_result(conn, asset_id=asset_id)
                if active is not None:
                    conn.commit()
                    return ProcessedResultBackfillOutcome(
                        asset_id=asset_id,
                        status="already_active",
                    )

                result, created = insert_ready_processed_result(
                    conn,
                    asset_id=asset["id"],
                    derived_file_id=current_derived_file["id"],
                    mime_type=inspected.mime_type,
                    size_bytes=inspected.size_bytes,
                    sha256=inspected.sha256,
                )
                if not created:
                    clear_active_processed_result(conn, asset_id=asset_id)
                set_active_processed_result(conn, asset_id=asset_id, result_id=result["id"])
                conn.commit()
                return ProcessedResultBackfillOutcome(
                    asset_id=asset_id,
                    status="created",
                )
            except Exception:
                conn.rollback()
                raise
    except sqlite3.DatabaseError:
        return ProcessedResultBackfillOutcome(
            asset_id=asset_id,
            status="retryable_failure",
            error_code=BACKFILL_RETRYABLE_FAILURE_CODE,
        )


def persist_backfill_outcome(
    *,
    settings: Settings,
    outcome: ProcessedResultBackfillOutcome,
) -> None:
    """Finish the current Phase 2A preview job without retrying its lease."""
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if outcome.status in {"created", "already_active"}:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'done', error_message = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE asset_id = ?
                      AND job_type = 'preview'
                      AND status != 'done'
                    """,
                    (outcome.asset_id,),
                )
            elif outcome.status == "integrity_failed":
                conn.execute(
                    """
                    UPDATE assets
                    SET preview_status = 'failed',
                        delete_candidate_status = 'not_candidate',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (outcome.asset_id,),
                )
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed', error_message = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE asset_id = ?
                      AND job_type = 'preview'
                      AND status != 'done'
                    """,
                    (BACKFILL_INTEGRITY_FAILURE_CODE, outcome.asset_id),
                )
            elif outcome.status == "retryable_failure":
                conn.execute(
                    """
                    UPDATE assets
                    SET preview_status = 'preview_ready',
                        delete_candidate_status = 'not_candidate',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (outcome.asset_id,),
                )
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed', error_message = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE asset_id = ?
                      AND job_type = 'preview'
                      AND status != 'done'
                    """,
                    (BACKFILL_RETRYABLE_FAILURE_CODE, outcome.asset_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
