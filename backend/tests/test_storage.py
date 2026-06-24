import os

import pytest

from app.services.storage import (
    REQUIRED_DIRECTORIES,
    StorageError,
    generate_original_relative_path,
    initialize_storage,
    resolve_media_path,
)


def test_initialize_storage_creates_required_directories(tmp_path):
    media_root = tmp_path / "media"

    initialize_storage(media_root)

    for directory in REQUIRED_DIRECTORIES:
        assert (media_root / directory).is_dir()


def test_initialize_storage_rejects_file_media_root(tmp_path):
    media_root = tmp_path / "media"
    media_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(StorageError):
        initialize_storage(media_root)


def test_initialize_storage_rejects_unwritable_media_root(tmp_path):
    media_root = tmp_path / "media"
    tmp_dir = media_root / "tmp"
    tmp_dir.mkdir(parents=True)
    original_mode = tmp_dir.stat().st_mode
    tmp_dir.chmod(0o500)
    try:
        if os.access(tmp_dir, os.W_OK):
            pytest.skip("local filesystem permissions allow writes despite chmod")
        with pytest.raises(StorageError):
            initialize_storage(media_root)
    finally:
        tmp_dir.chmod(original_mode)


def test_resolve_media_path_rejects_path_traversal(tmp_path):
    media_root = tmp_path / "media"
    initialize_storage(media_root)

    with pytest.raises(StorageError):
        resolve_media_path(media_root, "../outside.txt")


def test_generate_original_relative_path_uses_backend_generated_name():
    relative_path = generate_original_relative_path("../client-name.JPG")

    assert relative_path.startswith("originals/")
    assert "client-name" not in relative_path
    assert relative_path.endswith(".jpg")
