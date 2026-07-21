import sqlite3
from typing import Callable

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.assets import PREVIEW_STATUS_PREVIEW_READY, get_asset, update_preview_status
from app.repositories.derived_files import (
    DERIVED_KIND_PREVIEW,
    get_derived_file,
    insert_derived_file,
)
from app.repositories.jobs import set_job_done_in_transaction
from app.repositories.processed_results import (
    clear_active_processed_result,
    get_active_processed_result,
    insert_ready_processed_result,
    is_phase2a_session_video_asset,
    set_active_processed_result,
)


FaultInjector = Callable[[str], None]


class ProcessedResultFinalizationError(RuntimeError):
    pass


def finalize_ready_processed_result(
    *,
    settings: Settings,
    job_id: int,
    asset_id: int,
    preview_relative_path: str,
    mime_type: str,
    size_bytes: int,
    sha256: str | None = None,
    existing_derived_file_id: int | None = None,
    fault_injector: FaultInjector | None = None,
) -> None:
    """Commit preview visibility and its Phase 2A delivery result atomically."""
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            asset = get_asset(conn, asset_id)
            if asset is None:
                raise ProcessedResultFinalizationError("asset missing")

            if existing_derived_file_id is None:
                derived_file = insert_derived_file(
                    conn,
                    asset_id=asset_id,
                    kind=DERIVED_KIND_PREVIEW,
                    path=preview_relative_path,
                    mime_type=mime_type,
                    size_bytes=size_bytes,
                )
            else:
                derived_file = get_derived_file(conn, existing_derived_file_id)
                if derived_file is None:
                    raise ProcessedResultFinalizationError("preview record missing")
                _validate_existing_preview(
                    derived_file=derived_file,
                    asset_id=asset_id,
                    preview_relative_path=preview_relative_path,
                    mime_type=mime_type,
                    size_bytes=size_bytes,
                )
            _inject(fault_injector, "after_derived_file")

            if is_phase2a_session_video_asset(conn, asset_id=asset_id):
                if sha256 is None:
                    raise ProcessedResultFinalizationError("processed result hash missing")
                active = get_active_processed_result(conn, asset_id=asset_id)
                if active is None or active["derived_file_id"] != derived_file["id"]:
                    if active is not None:
                        clear_active_processed_result(conn, asset_id=asset_id)
                        _inject(fault_injector, "after_old_result_superseded")
                    result, _created = insert_ready_processed_result(
                        conn,
                        asset_id=asset_id,
                        derived_file_id=derived_file["id"],
                        mime_type=mime_type,
                        size_bytes=size_bytes,
                        sha256=sha256,
                    )
                    _inject(fault_injector, "after_ready_result")
                    set_active_processed_result(
                        conn,
                        asset_id=asset_id,
                        result_id=result["id"],
                    )
                    _inject(fault_injector, "after_active_pointer")

            update_preview_status(conn, asset_id, PREVIEW_STATUS_PREVIEW_READY)
            _inject(fault_injector, "after_preview_status")
            set_job_done_in_transaction(conn, job_id=job_id)
            _inject(fault_injector, "after_job_done")
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _validate_existing_preview(
    *,
    derived_file: dict,
    asset_id: int,
    preview_relative_path: str,
    mime_type: str,
    size_bytes: int,
) -> None:
    if (
        derived_file["asset_id"] != asset_id
        or derived_file["kind"] != DERIVED_KIND_PREVIEW
        or derived_file["path"] != preview_relative_path
        or derived_file["mime_type"] != mime_type
        or derived_file["size_bytes"] != size_bytes
    ):
        raise ProcessedResultFinalizationError("preview record mismatch")


def _inject(fault_injector: FaultInjector | None, step: str) -> None:
    if fault_injector is not None:
        fault_injector(step)
