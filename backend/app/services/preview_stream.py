from pathlib import Path

from fastapi.responses import StreamingResponse

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.assets import PREVIEW_STATUS_PREVIEW_READY, get_asset
from app.repositories.derived_files import get_preview_for_asset
from app.services.media_range import (
    ByteRange,
    InvalidRangeError,
    iter_file as _iter_file,
    parse_range_header,
)
from app.services.storage import StorageError, resolve_media_path


class PreviewNotFoundError(RuntimeError):
    pass


class PreviewNotReadyError(RuntimeError):
    pass


class PreviewStorageError(RuntimeError):
    pass


def open_preview_stream(
    *,
    settings: Settings,
    asset_id: int,
    range_header: str | None,
) -> StreamingResponse:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        asset = get_asset(conn, asset_id)
        if asset is None:
            raise PreviewNotFoundError("asset not found")
        if bool(asset["is_log"]) or asset["preview_status"] != PREVIEW_STATUS_PREVIEW_READY:
            raise PreviewNotReadyError("preview is not ready")
        preview = get_preview_for_asset(conn, asset_id)
        if preview is None:
            raise PreviewNotFoundError("preview not found")

    preview_path, mime_type, total_size = _validate_preview_file(settings, preview)
    byte_range = parse_range_header(range_header, total_size)

    if byte_range is None:
        headers = {
            "Content-Length": str(total_size),
            "Accept-Ranges": "bytes",
        }
        return StreamingResponse(
            _iter_file(preview_path, start=0, end=total_size - 1),
            status_code=200,
            media_type=mime_type,
            headers=headers,
        )

    headers = {
        "Content-Length": str(byte_range.length),
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {byte_range.start}-{byte_range.end}/{byte_range.total}",
    }
    return StreamingResponse(
        _iter_file(preview_path, start=byte_range.start, end=byte_range.end),
        status_code=206,
        media_type=mime_type,
        headers=headers,
    )
def _validate_preview_file(
    settings: Settings,
    preview: dict,
) -> tuple[Path, str, int]:
    mime_type = preview["mime_type"]
    if mime_type is None or str(mime_type).strip() == "":
        raise PreviewStorageError("preview storage failure")

    try:
        preview_path = resolve_media_path(settings.media_root, str(preview["path"]))
    except StorageError as exc:
        raise PreviewStorageError("preview storage failure") from exc

    try:
        stat = preview_path.stat()
    except OSError as exc:
        raise PreviewStorageError("preview storage failure") from exc
    if not preview_path.is_file():
        raise PreviewStorageError("preview storage failure")

    return preview_path, str(mime_type), stat.st_size
