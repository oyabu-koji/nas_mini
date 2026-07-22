from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.assets import get_asset
from app.repositories.derived_files import insert_derived_file
from app.repositories.jobs import set_job_done_in_transaction
from app.repositories.processed_results import (
    get_active_processed_result,
    insert_ready_processed_result,
    insert_superseded_processed_result,
    set_active_processed_result,
)
from app.repositories.rendition_provenance import insert_rendition_provenance
from app.repositories.renditions import (
    complete_rendition_in_transaction,
    get_rendition_by_job,
)
from app.services.processed_result_delivery import is_phase2a_deliverable_asset
from app.services.processed_result_integrity import hash_file_sha256
from app.services.storage import StorageError, promote_rendition_candidate


FaultInjector = Callable[[str], None]


class RenditionFinalizationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TransformEvidence:
    applied_preset_id: str
    transform_kind: str
    color_transform_status: str
    color_transform_error_code: str | None


def finalize_rendition_output(
    *,
    settings: Settings,
    job_id: int,
    rendition_id: str,
    candidate_path: Path,
    evidence: TransformEvidence,
    fault_injector: FaultInjector | None = None,
) -> None:
    final_path: Path | None = None
    try:
        relative_path, final_path = promote_rendition_candidate(
            settings.media_root,
            candidate_path=candidate_path,
            rendition_id=rendition_id,
        )
        try:
            file_stat = final_path.stat()
            sha256 = hash_file_sha256(final_path)
        except OSError as exc:
            raise RenditionFinalizationError("rendition_storage_failed") from exc
        if not final_path.is_file() or file_stat.st_size <= 0:
            raise RenditionFinalizationError("rendition_storage_failed")

        with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rendition = get_rendition_by_job(conn, job_id)
                if rendition is None or rendition["id"] != rendition_id:
                    raise RenditionFinalizationError("rendition_relation_invalid")
                asset = get_asset(conn, int(rendition["asset_id"]))
                if (
                    asset is None
                    or rendition["state"] != "finalizing"
                    or not is_phase2a_deliverable_asset(conn=conn, asset=asset)
                ):
                    raise RenditionFinalizationError("rendition_asset_not_eligible")
                current_generation = (
                    rendition["selection_generation"]
                    == asset["rendition_selection_generation"]
                )
                active = get_active_processed_result(conn, asset_id=int(asset["id"]))
                if active is None:
                    raise RenditionFinalizationError("rendition_asset_not_eligible")
                if current_generation and (
                    rendition["base_result_id"] is not None
                    and (
                        active["id"] != rendition["base_result_id"]
                        or active["derived_file_id"] != rendition["base_derived_file_id"]
                        or active["sha256"] != rendition["base_result_sha256"]
                    )
                ):
                    raise RenditionFinalizationError("rendition_asset_not_eligible")

                derived = insert_derived_file(
                    conn,
                    asset_id=int(asset["id"]),
                    kind="rendition",
                    path=relative_path,
                    mime_type="video/mp4",
                    size_bytes=file_stat.st_size,
                )
                _inject(fault_injector, "after_derived_file")
                if current_generation:
                    result, _created = insert_ready_processed_result(
                        conn,
                        asset_id=int(asset["id"]),
                        derived_file_id=derived["id"],
                        mime_type="video/mp4",
                        size_bytes=file_stat.st_size,
                        sha256=sha256,
                        preview_generation=None,
                    )
                    terminal_state = "ready"
                else:
                    result = insert_superseded_processed_result(
                        conn,
                        asset_id=int(asset["id"]),
                        derived_file_id=derived["id"],
                        mime_type="video/mp4",
                        size_bytes=file_stat.st_size,
                        sha256=sha256,
                    )
                    terminal_state = "superseded"
                _inject(fault_injector, "after_result")
                insert_rendition_provenance(
                    conn,
                    rendition=rendition,
                    result_id=result["id"],
                    derived_file_id=derived["id"],
                    applied_preset_id=evidence.applied_preset_id,
                    transform_kind=evidence.transform_kind,
                    color_transform_status=evidence.color_transform_status,
                    color_transform_error_code=evidence.color_transform_error_code,
                )
                _inject(fault_injector, "after_provenance")
                if current_generation:
                    set_active_processed_result(
                        conn, asset_id=int(asset["id"]), result_id=result["id"]
                    )
                    _inject(fault_injector, "after_active_pointer")
                complete_rendition_in_transaction(
                    conn,
                    rendition_id=rendition_id,
                    state=terminal_state,
                    result_id=result["id"],
                    applied_preset_id=evidence.applied_preset_id,
                    color_transform_status=evidence.color_transform_status,
                    error_code=evidence.color_transform_error_code,
                )
                _inject(fault_injector, "after_rendition")
                set_job_done_in_transaction(conn, job_id=job_id)
                _inject(fault_injector, "after_job")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    except StorageError as exc:
        if final_path is not None:
            final_path.unlink(missing_ok=True)
        raise RenditionFinalizationError("rendition_storage_failed") from exc
    except Exception:
        if final_path is not None:
            final_path.unlink(missing_ok=True)
        raise


def _inject(injector: FaultInjector | None, step: str) -> None:
    if injector is not None:
        injector(step)
