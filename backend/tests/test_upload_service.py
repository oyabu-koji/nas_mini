import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.schemas.assets import parse_upload_metadata
from app.services.storage import initialize_storage
from app.services import upload as upload_service
from app.services.upload import UploadTooLargeError, compute_sha256, save_upload_to_tmp


def _upload_file(content: bytes, filename: str = "client.mov") -> UploadFile:
    return UploadFile(file=BytesIO(content), filename=filename)


def _settings(tmp_path) -> Settings:
    return Settings(
        media_root=tmp_path / "media",
        api_token="secret-token",
        database_path=tmp_path / "db.sqlite3",
    )


def test_save_upload_to_tmp_removes_tmp_file_when_too_large(tmp_path):
    settings = _settings(tmp_path)
    initialize_storage(settings.media_root)
    tmp_path = settings.media_root / "tmp" / "upload.tmp"

    with pytest.raises(UploadTooLargeError):
        asyncio.run(
            save_upload_to_tmp(
                _upload_file(b"too-large"),
                tmp_path,
                max_bytes=3,
            )
        )

    assert not tmp_path.exists()


def test_create_upload_asset_saves_original_and_records_sha256(tmp_path):
    settings = _settings(tmp_path)
    initialize_storage(settings.media_root)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        run_migrations(conn)

    content = b"video-content"
    metadata = parse_upload_metadata(
        asset_type="video",
        filename="../../client.mov",
        taken_at=None,
        latitude=None,
        longitude=None,
        exif_json=None,
        is_log="0",
    )

    response = asyncio.run(
        upload_service.create_upload_asset(
            settings=settings,
            upload_file=_upload_file(content),
            metadata=metadata,
        )
    )

    assert response.asset.original_path.startswith("originals/")
    assert "../../client" not in response.asset.original_path
    assert response.asset.server_sha256 == compute_sha256(
        settings.media_root / response.asset.original_path
    )
    assert (settings.media_root / response.asset.original_path).read_bytes() == content


def test_create_upload_asset_cleans_original_when_db_insert_fails(
    monkeypatch,
    tmp_path,
):
    settings = _settings(tmp_path)
    initialize_storage(settings.media_root)

    def raise_insert_error(*args, **kwargs):
        raise RuntimeError("database insert failed")

    monkeypatch.setattr(upload_service, "insert_asset", raise_insert_error)
    metadata = parse_upload_metadata(
        asset_type="image",
        filename="photo.jpg",
        taken_at=None,
        latitude=None,
        longitude=None,
        exif_json=None,
        is_log=None,
    )

    with pytest.raises(RuntimeError):
        asyncio.run(
            upload_service.create_upload_asset(
                settings=settings,
                upload_file=_upload_file(b"image-content", filename="photo.jpg"),
                metadata=metadata,
            )
        )

    assert list((settings.media_root / "originals").iterdir()) == []


def test_cleanup_failure_log_does_not_include_host_absolute_path(
    caplog,
    monkeypatch,
    tmp_path,
):
    host_path = tmp_path / "media" / "originals" / "saved.jpg"
    relative_path = "originals/saved.jpg"

    def raise_cleanup_error(self):
        raise OSError(13, "Permission denied", str(host_path))

    monkeypatch.setattr(Path, "unlink", raise_cleanup_error)

    upload_service._delete_original_after_failure(host_path, relative_path)

    assert str(host_path) not in caplog.text
    assert relative_path in caplog.text
