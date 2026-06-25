from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from fastapi.responses import StreamingResponse

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.assets import PREVIEW_STATUS_PREVIEW_READY, get_asset
from app.repositories.derived_files import get_preview_for_asset
from app.services.storage import StorageError, resolve_media_path


CHUNK_SIZE_BYTES = 1024 * 1024


class PreviewNotFoundError(RuntimeError):
    pass


class PreviewNotReadyError(RuntimeError):
    pass


class PreviewStorageError(RuntimeError):
    pass


class InvalidRangeError(RuntimeError):
    def __init__(self, total_size: int):
        super().__init__("invalid range")
        self.total_size = total_size


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int
    total: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_range_header(range_header: str | None, total_size: int) -> ByteRange | None:
    if range_header is None or range_header.strip() == "":
        return None
    if total_size <= 0:
        raise InvalidRangeError(total_size)

    normalized = range_header.strip()
    if not normalized.startswith("bytes="):
        raise InvalidRangeError(total_size)

    range_spec = normalized.removeprefix("bytes=")
    if "," in range_spec or "-" not in range_spec:
        raise InvalidRangeError(total_size)

    start_text, end_text = range_spec.split("-", 1)
    if start_text == "" and end_text == "":
        raise InvalidRangeError(total_size)

    if start_text == "":
        return _suffix_range(end_text, total_size)

    try:
        start = int(start_text)
    except ValueError as exc:
        raise InvalidRangeError(total_size) from exc
    if start < 0 or start >= total_size:
        raise InvalidRangeError(total_size)

    if end_text == "":
        return ByteRange(start=start, end=total_size - 1, total=total_size)

    try:
        end = int(end_text)
    except ValueError as exc:
        raise InvalidRangeError(total_size) from exc
    if end < start:
        raise InvalidRangeError(total_size)
    return ByteRange(start=start, end=min(end, total_size - 1), total=total_size)


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
        if asset["preview_status"] != PREVIEW_STATUS_PREVIEW_READY:
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


def _suffix_range(end_text: str, total_size: int) -> ByteRange:
    try:
        suffix_length = int(end_text)
    except ValueError as exc:
        raise InvalidRangeError(total_size) from exc
    if suffix_length <= 0:
        raise InvalidRangeError(total_size)

    length = min(suffix_length, total_size)
    start = total_size - length
    return ByteRange(start=start, end=total_size - 1, total=total_size)


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


def _iter_file(path: Path, *, start: int, end: int) -> Iterator[bytes]:
    with path.open("rb") as file:
        file.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = file.read(min(CHUNK_SIZE_BYTES, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
