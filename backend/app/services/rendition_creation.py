from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.assets import get_asset
from app.repositories.derived_files import get_derived_file
from app.repositories.jobs import insert_or_return_job
from app.repositories.processed_results import (
    get_active_processed_result,
)
from app.repositories.renditions import (
    generate_rendition_id,
    get_rendition_by_client_request,
    get_rendition_for_asset,
    increment_selection_generation,
    insert_rendition,
    serialize_rendition,
)
from app.services.preset_registry import classify_preset
from app.services.processed_result_delivery import (
    is_phase2a_deliverable_asset,
    resolve_deliverable_result,
)
from app.services.processed_result_integrity import hash_file_sha256
from app.services.storage import StorageError, resolve_media_path


PreTransactionHook = Callable[[], None]
WriteFaultInjector = Callable[[str], None]


class RenditionCreationError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool, http_status: int):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.http_status = http_status


@dataclass(frozen=True)
class CreationResult:
    representation: dict[str, Any]
    replayed: bool


@dataclass(frozen=True)
class PreflightIdentity:
    asset_id: int
    original_path: str
    original_size: int
    original_sha256: str
    active_result_id: str
    active_derived_file_id: int
    active_result_size: int
    active_result_sha256: str
    active_derived_path: str


def create_rendition(
    *,
    settings: Settings,
    asset_id: int,
    client_request_id: str,
    preset_id: str,
    pre_transaction_hook: PreTransactionHook | None = None,
    write_fault_injector: WriteFaultInjector | None = None,
) -> CreationResult:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        replay = get_rendition_by_client_request(conn, client_request_id)
        if replay is not None:
            return _resolve_replay(replay, asset_id=asset_id, preset_id=preset_id)
        asset = get_asset(conn, asset_id)
        if asset is None:
            raise RenditionCreationError("asset_not_found", retryable=False, http_status=404)
        identity = _preflight_identity(settings=settings, conn=conn, asset=asset)

    snapshot = classify_preset(settings, preset_id)
    if pre_transaction_hook is not None:
        pre_transaction_hook()

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            replay = get_rendition_by_client_request(conn, client_request_id)
            if replay is not None:
                result = _resolve_replay(replay, asset_id=asset_id, preset_id=preset_id)
                conn.commit()
                return result
            current_asset = get_asset(conn, asset_id)
            if current_asset is None or not _database_eligible(
                settings, conn, current_asset
            ):
                raise RenditionCreationError(
                    "rendition_asset_not_eligible", retryable=False, http_status=409
                )
            if _database_identity(conn, current_asset) != identity:
                raise RenditionCreationError(
                    "rendition_precondition_changed", retryable=True, http_status=409
                )

            generation = increment_selection_generation(conn, asset_id=asset_id)
            _inject(write_fault_injector, "after_generation")
            rendition_id = generate_rendition_id()
            job, created = insert_or_return_job(
                conn,
                job_type="rendition",
                asset_id=asset_id,
                payload_json=json.dumps(
                    {"rendition_id": rendition_id}, separators=(",", ":"), sort_keys=True
                ),
                dedup_key=f"rendition:{rendition_id}",
            )
            if not created:
                raise RuntimeError("new rendition job unexpectedly existed")
            _inject(write_fault_injector, "after_job")
            rendition = insert_rendition(
                conn,
                rendition_id=rendition_id,
                asset_id=asset_id,
                client_request_id=client_request_id,
                job_id=job["id"],
                selection_generation=generation,
                snapshot=snapshot,
                base_result_id=identity.active_result_id,
                base_derived_file_id=identity.active_derived_file_id,
                base_result_sha256=identity.active_result_sha256,
            )
            _inject(write_fault_injector, "after_rendition")
            conn.commit()
            return CreationResult(serialize_rendition(rendition), replayed=False)
        except Exception:
            conn.rollback()
            raise


def get_rendition_representation(
    *, settings: Settings, asset_id: int, rendition_id: str
) -> dict[str, Any]:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        rendition = get_rendition_for_asset(
            conn, asset_id=asset_id, rendition_id=rendition_id
        )
    if rendition is None:
        raise RenditionCreationError("rendition_not_found", retryable=False, http_status=404)
    return serialize_rendition(rendition)


def _resolve_replay(
    rendition: dict[str, Any], *, asset_id: int, preset_id: str
) -> CreationResult:
    if rendition["asset_id"] != asset_id or rendition["requested_preset_id"] != preset_id:
        raise RenditionCreationError(
            "rendition_request_conflict", retryable=False, http_status=409
        )
    return CreationResult(serialize_rendition(rendition), replayed=True)


def _preflight_identity(*, settings: Settings, conn, asset: dict[str, Any]) -> PreflightIdentity:
    deliverable = resolve_deliverable_result(settings=settings, conn=conn, asset=asset)
    if deliverable is None:
        raise RenditionCreationError(
            "rendition_asset_not_eligible", retryable=False, http_status=409
        )
    original_path = asset.get("original_path")
    original_size = asset.get("size_bytes")
    original_sha256 = asset.get("server_sha256")
    if (
        not isinstance(original_path, str)
        or not original_path.startswith("originals/")
        or not isinstance(original_size, int)
        or original_size <= 0
        or not isinstance(original_sha256, str)
        or len(original_sha256) != 64
    ):
        raise RenditionCreationError(
            "rendition_asset_not_eligible", retryable=False, http_status=409
        )
    try:
        path = resolve_media_path(settings.media_root, original_path)
        file_stat = path.stat()
        actual_sha256 = hash_file_sha256(path)
    except (StorageError, OSError):
        raise RenditionCreationError(
            "rendition_asset_not_eligible", retryable=False, http_status=409
        ) from None
    if not path.is_file() or file_stat.st_size != original_size or actual_sha256 != original_sha256:
        raise RenditionCreationError(
            "rendition_asset_not_eligible", retryable=False, http_status=409
        )
    result = deliverable.result
    derived = deliverable.derived_file
    return PreflightIdentity(
        asset_id=int(asset["id"]),
        original_path=original_path,
        original_size=original_size,
        original_sha256=original_sha256,
        active_result_id=result["id"],
        active_derived_file_id=int(derived["id"]),
        active_result_size=int(result["size_bytes"]),
        active_result_sha256=str(result["sha256"]),
        active_derived_path=str(derived["path"]),
    )


def _database_eligible(
    settings: Settings, conn, asset: dict[str, Any]
) -> bool:
    if "formal_preview_id" in asset:
        return resolve_deliverable_result(
            settings=settings, conn=conn, asset=asset
        ) is not None
    if not is_phase2a_deliverable_asset(conn=conn, asset=asset):
        return False
    active = get_active_processed_result(conn, asset_id=int(asset["id"]))
    if active is None or active.get("status") != "ready":
        return False
    derived_id = active.get("derived_file_id")
    if not isinstance(derived_id, int):
        return False
    derived = get_derived_file(conn, derived_id)
    return bool(
        derived
        and derived.get("asset_id") == asset["id"]
        and derived.get("kind") in {"preview", "rendition"}
        and derived.get("mime_type") == "video/mp4"
    )


def _database_identity(conn, asset: dict[str, Any]) -> PreflightIdentity | None:
    active = get_active_processed_result(conn, asset_id=int(asset["id"]))
    if active is None or not isinstance(active.get("derived_file_id"), int):
        return None
    derived = get_derived_file(conn, int(active["derived_file_id"]))
    if derived is None:
        return None
    return PreflightIdentity(
        asset_id=int(asset["id"]),
        original_path=str(asset.get("original_path")),
        original_size=int(asset.get("size_bytes") or 0),
        original_sha256=str(asset.get("server_sha256")),
        active_result_id=str(active["id"]),
        active_derived_file_id=int(derived["id"]),
        active_result_size=int(active.get("size_bytes") or 0),
        active_result_sha256=str(active.get("sha256")),
        active_derived_path=str(derived.get("path")),
    )


def _inject(injector: WriteFaultInjector | None, step: str) -> None:
    if injector is not None:
        injector(step)
