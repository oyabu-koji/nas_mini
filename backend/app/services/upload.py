import hashlib
import json
import logging
from pathlib import Path

from fastapi import UploadFile

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.assets import insert_asset
from app.repositories.jobs import insert_job
from app.schemas.assets import (
    AssetResponse,
    JobResponse,
    UploadAssetResponse,
    UploadMetadata,
    exif_json_from_text,
    exif_json_to_text,
)
from app.services.storage import (
    generate_original_relative_path,
    generate_tmp_upload_path,
    resolve_media_path,
)


MAX_UPLOAD_SIZE_BYTES = 104_857_600
UPLOAD_CHUNK_SIZE_BYTES = 1024 * 1024

logger = logging.getLogger(__name__)


class UploadTooLargeError(RuntimeError):
    pass


async def save_upload_to_tmp(
    upload_file: UploadFile,
    tmp_path: Path,
    max_bytes: int = MAX_UPLOAD_SIZE_BYTES,
) -> int:
    size_bytes = 0
    try:
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("wb") as output_file:
            while True:
                chunk = await upload_file.read(UPLOAD_CHUNK_SIZE_BYTES)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise UploadTooLargeError("upload exceeds maximum size")
                output_file.write(chunk)
    except Exception:
        _unlink_if_exists(tmp_path)
        raise
    return size_bytes


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(UPLOAD_CHUNK_SIZE_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def create_upload_asset(
    *,
    settings: Settings,
    upload_file: UploadFile,
    metadata: UploadMetadata,
) -> UploadAssetResponse:
    tmp_path = generate_tmp_upload_path(settings.media_root)
    original_relative_path = generate_original_relative_path(metadata.filename)
    original_path = resolve_media_path(settings.media_root, original_relative_path)
    original_saved = False
    db_committed = False

    size_bytes = await save_upload_to_tmp(upload_file, tmp_path, MAX_UPLOAD_SIZE_BYTES)

    try:
        original_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.replace(original_path)
        original_saved = True

        server_sha256 = compute_sha256(original_path)
        exif_json_text = exif_json_to_text(metadata.exif_json)
        job_type = _preview_job_type(metadata)

        with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
            with conn:
                asset = insert_asset(
                    conn,
                    type=metadata.type,
                    filename=metadata.filename,
                    original_path=original_relative_path,
                    size_bytes=size_bytes,
                    server_sha256=server_sha256,
                    taken_at=metadata.taken_at,
                    latitude=metadata.latitude,
                    longitude=metadata.longitude,
                    exif_json=exif_json_text,
                    is_log=metadata.is_log,
                )
                job = insert_job(
                    conn,
                    job_type=job_type,
                    asset_id=asset["id"],
                    payload_json=_job_payload_json(
                        asset_id=asset["id"],
                        original_path=original_relative_path,
                        asset_type=metadata.type,
                        is_log=metadata.is_log,
                    ),
                )
            db_committed = True

        return _build_response(asset, job)
    except Exception:
        if original_saved and not db_committed:
            _delete_original_after_failure(original_path, original_relative_path)
        else:
            _unlink_if_exists(tmp_path)
        raise


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _preview_job_type(metadata: UploadMetadata) -> str:
    if metadata.type == "video" and metadata.is_log:
        return "lut_preview"
    return "preview"


def _job_payload_json(
    *,
    asset_id: int,
    original_path: str,
    asset_type: str,
    is_log: bool,
) -> str:
    return json.dumps(
        {
            "asset_id": asset_id,
            "original_path": original_path,
            "type": asset_type,
            "is_log": is_log,
        },
        separators=(",", ":"),
    )


def _build_response(
    asset: dict[str, object],
    job: dict[str, object],
) -> UploadAssetResponse:
    asset_response = AssetResponse(
        id=int(asset["id"]),
        type=str(asset["type"]),
        filename=str(asset["filename"]),
        original_path=str(asset["original_path"]),
        size_bytes=int(asset["size_bytes"]),
        server_sha256=str(asset["server_sha256"]),
        taken_at=asset["taken_at"],  # type: ignore[arg-type]
        latitude=asset["latitude"],  # type: ignore[arg-type]
        longitude=asset["longitude"],  # type: ignore[arg-type]
        exif_json=exif_json_from_text(asset["exif_json"]),  # type: ignore[arg-type]
        is_log=bool(asset["is_log"]),
        transfer_status=str(asset["transfer_status"]),
        verification_status=str(asset["verification_status"]),
        preview_status=str(asset["preview_status"]),
        review_status=str(asset["review_status"]),
        delete_candidate_status=str(asset["delete_candidate_status"]),
    )
    job_response = JobResponse(
        id=int(job["id"]),
        job_type=str(job["job_type"]),
        status=str(job["status"]),
        asset_id=job["asset_id"],  # type: ignore[arg-type]
    )
    return UploadAssetResponse(
        asset=asset_response,
        job=job_response,
        server_sha256=asset_response.server_sha256,
        transfer_status=asset_response.transfer_status,
        verification_status=asset_response.verification_status,
        preview_status=asset_response.preview_status,
        review_status=asset_response.review_status,
        delete_candidate_status=asset_response.delete_candidate_status,
    )


def _delete_original_after_failure(path: Path, relative_path: str) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning(
            "Saved original cleanup failed after upload database failure: %s (%s, errno=%s)",
            relative_path,
            exc.__class__.__name__,
            exc.errno,
        )
