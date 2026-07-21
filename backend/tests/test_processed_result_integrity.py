import hashlib

import pytest

from app.core.settings import Settings
from app.services.processed_result_integrity import (
    ProcessedResultIntegrityError,
    hash_file_sha256,
    verify_processed_result,
)
from app.services.storage import initialize_storage


def _settings(tmp_path) -> Settings:
    return Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
    )


def _records(*, content: bytes, path: str = "previews/result.mp4"):
    digest = hashlib.sha256(content).hexdigest()
    result = {
        "id": "a" * 32,
        "asset_id": 1,
        "derived_file_id": 2,
        "status": "ready",
        "mime_type": "video/mp4",
        "size_bytes": len(content),
        "sha256": digest,
    }
    derived_file = {
        "id": 2,
        "asset_id": 1,
        "kind": "preview",
        "path": path,
        "mime_type": "video/mp4",
        "size_bytes": len(content),
    }
    return result, derived_file


def test_verify_processed_result_uses_confined_path_size_and_streaming_hash(tmp_path):
    settings = _settings(tmp_path)
    initialize_storage(settings.media_root)
    content = b"processed-video" * 100
    result, derived_file = _records(content=content)
    path = settings.media_root / derived_file["path"]
    path.write_bytes(content)

    verified = verify_processed_result(
        settings=settings,
        result=result,
        derived_file=derived_file,
    )

    assert verified.path == path
    assert verified.size_bytes == len(content)
    assert verified.sha256 == hashlib.sha256(content).hexdigest()
    assert hash_file_sha256(path, block_size=3) == verified.sha256


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda result, _derived: result.update(size_bytes=11), "processed_result_size_mismatch"),
        (lambda result, _derived: result.update(sha256="b" * 64), "processed_result_sha256_mismatch"),
        (lambda result, _derived: result.update(mime_type="video/quicktime"), "processed_result_unsupported_mime"),
        (lambda _result, derived: derived.update(path="../originals/input.mov"), "processed_result_invalid_storage_path"),
    ],
)
def test_verify_processed_result_rejects_invalid_metadata_without_path_leak(
    tmp_path,
    mutate,
    expected_code,
):
    settings = _settings(tmp_path)
    initialize_storage(settings.media_root)
    content = b"processed-video"
    result, derived_file = _records(content=content)
    (settings.media_root / derived_file["path"]).write_bytes(content)
    mutate(result, derived_file)

    with pytest.raises(ProcessedResultIntegrityError) as exc_info:
        verify_processed_result(settings=settings, result=result, derived_file=derived_file)

    assert exc_info.value.code == expected_code
    assert str(settings.media_root) not in str(exc_info.value)


def test_verify_processed_result_rejects_missing_file(tmp_path):
    settings = _settings(tmp_path)
    initialize_storage(settings.media_root)
    result, derived_file = _records(content=b"processed-video")

    with pytest.raises(ProcessedResultIntegrityError) as exc_info:
        verify_processed_result(settings=settings, result=result, derived_file=derived_file)

    assert exc_info.value.code == "processed_result_file_missing"
