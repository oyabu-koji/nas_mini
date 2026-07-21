import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Callable

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.assets import (
    PREVIEW_STATUS_PREVIEW_READY,
    get_asset,
    update_preview_status,
)
from app.repositories.derived_files import (
    get_preview_for_asset,
)
from app.repositories.jobs import fail_job_and_asset_preview, set_job_failed_in_transaction
from app.repositories.processed_results import is_phase2a_session_video_asset
from app.services import ffmpeg
from app.services.processed_result_finalizer import finalize_ready_processed_result
from app.services.processed_result_integrity import (
    ProcessedResultIntegrityError,
    hash_file_sha256,
    inspect_derived_preview,
)
from app.services.storage import (
    StorageError,
    generate_preview_relative_path,
    generate_tmp_preview_path,
    resolve_media_path,
)


PREVIEW_ERROR_MAX_LENGTH = 200

logger = logging.getLogger(__name__)


class PreviewProcessingError(RuntimeError):
    def __init__(self, message: str, asset_id: int | None = None):
        super().__init__(message)
        self.message = _sanitize_error(message)
        self.asset_id = asset_id


def process_preview_job(
    *,
    settings: Settings,
    job: dict[str, Any],
    run_ffmpeg: Callable[[list[str], int], None] = ffmpeg.run_ffmpeg,
) -> bool:
    tmp_preview_path: Path | None = None
    confirmed_preview_path: Path | None = None
    confirmed_preview_relative_path: str | None = None
    asset_id: int | None = None

    try:
        asset_id = _job_asset_id(job)
        _validate_payload_asset_id(job, asset_id)
        if job.get("job_type") == "lut_preview":
            raise PreviewProcessingError("LOG preview unavailable", asset_id)

        with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
            asset = get_asset(conn, asset_id) if asset_id is not None else None
            if asset is None:
                raise PreviewProcessingError("asset missing", asset_id)

            existing_preview = get_preview_for_asset(conn, asset_id)
            if existing_preview is not None:
                phase2a_result_expected = is_phase2a_session_video_asset(
                    conn,
                    asset_id=asset_id,
                )
                _handle_existing_preview(
                    settings=settings,
                    job_id=job["id"],
                    asset_id=asset_id,
                    existing_preview=existing_preview,
                    phase2a_result_expected=phase2a_result_expected,
                )
                return True

        preview_spec = _preview_spec(settings, job, asset)
        tmp_preview_path = generate_tmp_preview_path(settings.media_root, preview_spec["extension"])
        confirmed_preview_relative_path = generate_preview_relative_path(preview_spec["extension"])
        confirmed_preview_path = resolve_media_path(settings.media_root, confirmed_preview_relative_path)
        try:
            confirmed_preview_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PreviewProcessingError("storage failure", asset_id) from exc

        command = preview_spec["command_builder"](
            input_path=preview_spec["original_path"],
            output_path=tmp_preview_path,
        )
        run_ffmpeg(command, ffmpeg.DEFAULT_FFMPEG_TIMEOUT_SECONDS)
        try:
            tmp_preview_path.replace(confirmed_preview_path)
        except OSError as exc:
            raise PreviewProcessingError("storage failure", asset_id) from exc
        tmp_preview_path = None

        try:
            size_bytes = confirmed_preview_path.stat().st_size
        except OSError as exc:
            _cleanup_confirmed_preview(
                confirmed_preview_path,
                confirmed_preview_relative_path,
            )
            raise PreviewProcessingError("storage failure", asset_id) from exc

        sha256: str | None = None
        if preview_spec["mime_type"] == "video/mp4":
            try:
                sha256 = hash_file_sha256(confirmed_preview_path)
            except OSError as exc:
                _cleanup_confirmed_preview(
                    confirmed_preview_path,
                    confirmed_preview_relative_path,
                )
                raise PreviewProcessingError("storage failure", asset_id) from exc

        try:
            finalize_ready_processed_result(
                settings=settings,
                job_id=job["id"],
                asset_id=asset_id,
                preview_relative_path=confirmed_preview_relative_path,
                mime_type=preview_spec["mime_type"],
                size_bytes=size_bytes,
                sha256=sha256,
            )
        except Exception:
            _cleanup_confirmed_preview(
                confirmed_preview_path,
                confirmed_preview_relative_path,
            )
            _mark_failed(settings, job["id"], "database failure", asset_id)
            return True

        return True
    except PreviewProcessingError as exc:
        _cleanup_tmp_preview(tmp_preview_path)
        _mark_failed(settings, job["id"], exc.message, exc.asset_id)
        return True
    except ffmpeg.PreviewGenerationError as exc:
        _cleanup_tmp_preview(tmp_preview_path)
        _mark_failed(settings, job["id"], _sanitize_error(str(exc)), asset_id)
        return True
    except StorageError as exc:
        _cleanup_tmp_preview(tmp_preview_path)
        _mark_failed(settings, job["id"], _storage_error_message(exc), asset_id)
        return True
    except Exception:
        _cleanup_tmp_preview(tmp_preview_path)
        _mark_failed(settings, job["id"], "database failure", asset_id)
        return True


def _job_asset_id(job: dict[str, Any]) -> int | None:
    asset_id = job.get("asset_id")
    if asset_id is None:
        return None
    if type(asset_id) is not int:
        raise PreviewProcessingError("job asset invalid")
    return asset_id


def _validate_payload_asset_id(job: dict[str, Any], asset_id: int | None) -> None:
    payload_json = job.get("payload_json")
    if payload_json is None or (isinstance(payload_json, str) and payload_json.strip() == ""):
        return
    try:
        payload = json.loads(payload_json if isinstance(payload_json, str) else str(payload_json))
    except (TypeError, json.JSONDecodeError) as exc:
        raise PreviewProcessingError("job payload invalid", asset_id) from exc
    if not isinstance(payload, dict):
        raise PreviewProcessingError("job payload invalid", asset_id)
    if "asset_id" not in payload:
        return

    payload_asset_id = payload["asset_id"]
    if type(payload_asset_id) is not int or asset_id is None:
        raise PreviewProcessingError("job payload asset invalid", asset_id)
    if payload_asset_id != asset_id:
        raise PreviewProcessingError("job payload asset mismatch", asset_id)


def _handle_existing_preview(
    *,
    settings: Settings,
    job_id: int,
    asset_id: int,
    existing_preview: dict[str, Any],
    phase2a_result_expected: bool,
) -> None:
    sha256: str | None = None
    try:
        if existing_preview["mime_type"] == "video/mp4":
            inspected = inspect_derived_preview(settings=settings, derived_file=existing_preview)
            sha256 = inspected.sha256
        else:
            preview_path = resolve_media_path(settings.media_root, str(existing_preview["path"]))
            if not preview_path.is_file():
                raise PreviewProcessingError("preview file missing", asset_id)
    except ProcessedResultIntegrityError as exc:
        message = "preview file missing" if exc.code == "processed_result_file_missing" else "preview integrity failure"
        raise PreviewProcessingError(message, asset_id) from exc
    except StorageError as exc:
        raise PreviewProcessingError(_storage_error_message(exc), asset_id) from exc

    try:
        finalize_ready_processed_result(
            settings=settings,
            job_id=job_id,
            asset_id=asset_id,
            preview_relative_path=str(existing_preview["path"]),
            mime_type=str(existing_preview["mime_type"]),
            size_bytes=int(existing_preview["size_bytes"]),
            sha256=sha256,
            existing_derived_file_id=int(existing_preview["id"]),
        )
    except sqlite3.DatabaseError:
        if not phase2a_result_expected:
            raise
        _mark_existing_preview_retryable(settings=settings, job_id=job_id, asset_id=asset_id)


def _mark_existing_preview_retryable(*, settings: Settings, job_id: int, asset_id: int) -> None:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        with conn:
            update_preview_status(conn, asset_id, PREVIEW_STATUS_PREVIEW_READY)
            set_job_failed_in_transaction(
                conn,
                job_id=job_id,
                error_message="processed_result_backfill_retryable_failure",
            )


def _preview_spec(settings: Settings, job: dict[str, Any], asset: dict[str, Any]) -> dict[str, Any]:
    asset_id = int(asset["id"])
    asset_type = str(asset["type"])

    original_path = _resolve_existing_original(settings, str(asset["original_path"]), asset_id)

    if asset_type == "video":
        return {
            "extension": ".mp4",
            "mime_type": "video/mp4",
            "original_path": original_path,
            "command_builder": lambda *, input_path, output_path: ffmpeg.build_video_preview_command(
                input_path=input_path,
                output_path=output_path,
                lut_path=None,
            ),
        }

    if asset_type == "image":
        return {
            "extension": ".jpg",
            "mime_type": "image/jpeg",
            "original_path": original_path,
            "command_builder": ffmpeg.build_image_preview_command,
        }

    raise PreviewProcessingError("unsupported asset type", asset_id)


def _resolve_existing_original(settings: Settings, original_relative_path: str, asset_id: int) -> Path:
    try:
        original_path = resolve_media_path(settings.media_root, original_relative_path)
    except StorageError as exc:
        raise PreviewProcessingError(_storage_error_message(exc), asset_id) from exc

    if not original_path.is_file():
        raise PreviewProcessingError("original file missing", asset_id)
    return original_path


def _mark_failed(
    settings: Settings,
    job_id: int,
    error_message: str,
    asset_id: int | None,
) -> None:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        fail_job_and_asset_preview(
            conn,
            job_id=job_id,
            asset_id=asset_id,
            error_message=_sanitize_error(error_message),
        )


def _cleanup_tmp_preview(tmp_preview_path: Path | None) -> None:
    if tmp_preview_path is None:
        return
    try:
        tmp_preview_path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning(
            "Tmp preview cleanup failed (%s, errno=%s)",
            exc.__class__.__name__,
            exc.errno,
        )


def _cleanup_confirmed_preview(
    confirmed_preview_path: Path,
    confirmed_preview_relative_path: str,
) -> None:
    try:
        confirmed_preview_path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning(
            "Saved preview cleanup failed after database failure: %s (%s, errno=%s)",
            confirmed_preview_relative_path,
            exc.__class__.__name__,
            exc.errno,
        )


def _storage_error_message(exc: StorageError) -> str:
    text = str(exc)
    if "escapes" in text:
        return "unsafe original path"
    if "relative" in text:
        return "unsafe original path"
    return "storage failure"


def _sanitize_error(message: str) -> str:
    return message[:PREVIEW_ERROR_MAX_LENGTH]
