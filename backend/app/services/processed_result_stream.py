from typing import BinaryIO

from fastapi.responses import StreamingResponse

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.assets import get_asset
from app.repositories.processed_results import get_active_processed_result, get_processed_result
from app.services.media_range import InvalidRangeError, iter_open_file, parse_range_header
from app.services.processed_result_delivery import resolve_deliverable_result


class ProcessedResultNotFoundError(RuntimeError):
    pass


class ProcessedResultSupersededError(RuntimeError):
    pass


class ProcessedResultNotReadyError(RuntimeError):
    pass


class ProcessedResultRangeNotSatisfiableError(RuntimeError):
    def __init__(self, total_size: int):
        super().__init__("processed result range not satisfiable")
        self.total_size = total_size


def open_processed_result_stream(
    *,
    settings: Settings,
    asset_id: int,
    result_id: str,
    range_header: str | None,
) -> StreamingResponse:
    descriptor: BinaryIO | None = None
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        asset = get_asset(conn, asset_id)
        if asset is None:
            raise ProcessedResultNotFoundError()
        requested_result = get_processed_result(
            conn,
            asset_id=asset_id,
            result_id=result_id,
        )
        if requested_result is None:
            raise ProcessedResultNotFoundError()

        active_result = get_active_processed_result(conn, asset_id=asset_id)
        if active_result is None or active_result["id"] != result_id:
            _raise_for_inactive_result(requested_result)

        deliverable = resolve_deliverable_result(settings=settings, conn=conn, asset=asset)
        if deliverable is None or deliverable.result["id"] != result_id:
            raise ProcessedResultNotReadyError()

        total_size = deliverable.verified_file.size_bytes
        try:
            byte_range = parse_range_header(range_header, total_size)
        except InvalidRangeError as exc:
            raise ProcessedResultRangeNotSatisfiableError(exc.total_size) from exc

        try:
            conn.execute("BEGIN IMMEDIATE")
            current_asset = get_asset(conn, asset_id)
            current_result = get_processed_result(
                conn,
                asset_id=asset_id,
                result_id=result_id,
            )
            current_active = get_active_processed_result(conn, asset_id=asset_id)
            if current_asset is None or current_result is None:
                raise ProcessedResultNotFoundError()
            if current_active is None or current_active["id"] != result_id:
                _raise_for_inactive_result(current_result)

            descriptor = deliverable.verified_file.path.open("rb")
            conn.commit()
        except (ProcessedResultNotFoundError, ProcessedResultSupersededError, ProcessedResultNotReadyError):
            if descriptor is not None:
                descriptor.close()
            if conn.in_transaction:
                conn.rollback()
            raise
        except OSError as exc:
            if descriptor is not None:
                descriptor.close()
            if conn.in_transaction:
                conn.rollback()
            raise ProcessedResultNotReadyError() from exc
        except Exception:
            if descriptor is not None:
                descriptor.close()
            if conn.in_transaction:
                conn.rollback()
            raise

    if descriptor is None:
        raise ProcessedResultNotReadyError()

    headers = _identity_headers(result=deliverable.result)
    if byte_range is None:
        headers["Content-Length"] = str(total_size)
        return StreamingResponse(
            iter_open_file(descriptor, start=0, end=total_size - 1),
            status_code=200,
            media_type=deliverable.verified_file.mime_type,
            headers=headers,
        )

    headers["Content-Length"] = str(byte_range.length)
    headers["Content-Range"] = (
        f"bytes {byte_range.start}-{byte_range.end}/{byte_range.total}"
    )
    return StreamingResponse(
        iter_open_file(descriptor, start=byte_range.start, end=byte_range.end),
        status_code=206,
        media_type=deliverable.verified_file.mime_type,
        headers=headers,
    )


def _identity_headers(*, result: dict) -> dict[str, str]:
    result_id = str(result["id"])
    sha256 = str(result["sha256"])
    return {
        "Accept-Ranges": "bytes",
        "ETag": f'"{sha256}"',
        "X-Processed-Result-Id": result_id,
        "X-Processed-Result-SHA256": sha256,
        "X-Processed-Result-Size": str(result["size_bytes"]),
        "Content-Disposition": f'attachment; filename="processed-{result_id}.mp4"',
    }


def _raise_for_inactive_result(result: dict) -> None:
    if result["status"] == "superseded":
        raise ProcessedResultSupersededError()
    raise ProcessedResultNotReadyError()
