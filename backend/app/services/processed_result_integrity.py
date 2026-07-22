import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.settings import Settings
from app.repositories.processed_results import RESULT_STATUS_READY
from app.services.storage import StorageError, resolve_media_path


HASH_BLOCK_SIZE_BYTES = 1024 * 1024
PHASE2A_RESULT_MIME_TYPES = {"video/mp4"}


class ProcessedResultIntegrityError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VerifiedProcessedResult:
    path: Path
    mime_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class InspectedDerivedPreview:
    path: Path
    mime_type: str
    size_bytes: int
    sha256: str


def hash_file_sha256(path: Path, *, block_size: int = HASH_BLOCK_SIZE_BYTES) -> str:
    if block_size <= 0:
        raise ValueError("hash block size must be positive")

    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            block = file.read(block_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def verify_processed_result(
    *,
    settings: Settings,
    result: dict[str, Any],
    derived_file: dict[str, Any],
) -> VerifiedProcessedResult:
    _validate_record_identity(result=result, derived_file=derived_file)
    mime_type = _validate_mime_type(result=result, derived_file=derived_file)
    expected_size = _validate_size(result=result, derived_file=derived_file)
    expected_sha256 = _validate_sha256(result=result)
    inspected = inspect_derived_preview(settings=settings, derived_file=derived_file)
    if inspected.mime_type != mime_type:
        raise ProcessedResultIntegrityError("processed_result_mime_mismatch")
    if inspected.size_bytes != expected_size:
        raise ProcessedResultIntegrityError("processed_result_size_mismatch")
    if inspected.sha256 != expected_sha256:
        raise ProcessedResultIntegrityError("processed_result_sha256_mismatch")

    return VerifiedProcessedResult(
        path=inspected.path,
        mime_type=mime_type,
        size_bytes=expected_size,
        sha256=expected_sha256,
    )


def inspect_derived_preview(
    *,
    settings: Settings,
    derived_file: dict[str, Any],
) -> InspectedDerivedPreview:
    if derived_file.get("kind") not in {"preview", "rendition"}:
        raise ProcessedResultIntegrityError("processed_result_invalid_derived_file")
    mime_type = derived_file.get("mime_type")
    if mime_type not in PHASE2A_RESULT_MIME_TYPES:
        raise ProcessedResultIntegrityError("processed_result_unsupported_mime")
    expected_size = derived_file.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size <= 0:
        raise ProcessedResultIntegrityError("processed_result_invalid_size")
    path = _resolve_preview_path(settings=settings, derived_file=derived_file)

    try:
        stat = path.stat()
    except FileNotFoundError as exc:
        raise ProcessedResultIntegrityError("processed_result_file_missing") from exc
    except OSError as exc:
        raise ProcessedResultIntegrityError("processed_result_file_unavailable") from exc
    if not path.is_file():
        raise ProcessedResultIntegrityError("processed_result_file_missing")
    if stat.st_size != expected_size:
        raise ProcessedResultIntegrityError("processed_result_size_mismatch")

    try:
        sha256 = hash_file_sha256(path)
    except OSError as exc:
        raise ProcessedResultIntegrityError("processed_result_file_unavailable") from exc
    return InspectedDerivedPreview(
        path=path,
        mime_type=str(mime_type),
        size_bytes=expected_size,
        sha256=sha256,
    )


def _validate_record_identity(
    *,
    result: dict[str, Any],
    derived_file: dict[str, Any],
) -> None:
    if result.get("status") != RESULT_STATUS_READY:
        raise ProcessedResultIntegrityError("processed_result_not_ready")
    if result.get("derived_file_id") != derived_file.get("id"):
        raise ProcessedResultIntegrityError("processed_result_derived_file_mismatch")
    if result.get("asset_id") != derived_file.get("asset_id"):
        raise ProcessedResultIntegrityError("processed_result_asset_mismatch")
    if derived_file.get("kind") not in {"preview", "rendition"}:
        raise ProcessedResultIntegrityError("processed_result_invalid_derived_file")


def _validate_mime_type(*, result: dict[str, Any], derived_file: dict[str, Any]) -> str:
    mime_type = result.get("mime_type")
    if mime_type not in PHASE2A_RESULT_MIME_TYPES:
        raise ProcessedResultIntegrityError("processed_result_unsupported_mime")
    return str(mime_type)


def _validate_size(*, result: dict[str, Any], derived_file: dict[str, Any]) -> int:
    size_bytes = result.get("size_bytes")
    if not isinstance(size_bytes, int) or size_bytes <= 0:
        raise ProcessedResultIntegrityError("processed_result_invalid_size")
    return size_bytes


def _validate_sha256(*, result: dict[str, Any]) -> str:
    sha256 = result.get("sha256")
    if not isinstance(sha256, str) or len(sha256) != 64:
        raise ProcessedResultIntegrityError("processed_result_invalid_sha256")
    if any(character not in "0123456789abcdef" for character in sha256):
        raise ProcessedResultIntegrityError("processed_result_invalid_sha256")
    return sha256


def _resolve_preview_path(*, settings: Settings, derived_file: dict[str, Any]) -> Path:
    relative_path = derived_file.get("path")
    if not isinstance(relative_path, str) or not relative_path.startswith("previews/"):
        raise ProcessedResultIntegrityError("processed_result_invalid_storage_path")
    try:
        return resolve_media_path(settings.media_root, relative_path)
    except StorageError as exc:
        raise ProcessedResultIntegrityError("processed_result_invalid_storage_path") from exc
