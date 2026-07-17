import os

import pytest

from app.services.storage import (
    REQUIRED_DIRECTORIES,
    StorageError,
    cleanup_session_temporary_files,
    generate_original_relative_path,
    generate_preview_relative_path,
    generate_session_assembly_path,
    generate_session_chunk_path,
    generate_session_original_relative_path,
    generate_session_staging_path,
    generate_tmp_preview_path,
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


def test_generate_tmp_preview_path_stays_under_tmp(tmp_path):
    media_root = tmp_path / "media"
    initialize_storage(media_root)

    preview_path = generate_tmp_preview_path(media_root, ".mp4")

    assert preview_path.parent == media_root / "tmp"
    assert preview_path.suffix == ".mp4"


def test_generate_preview_relative_path_uses_backend_generated_name():
    relative_path = generate_preview_relative_path("jpg")

    assert relative_path.startswith("previews/")
    assert relative_path.endswith(".jpg")


def test_generate_preview_relative_path_rejects_unsupported_extension():
    with pytest.raises(StorageError):
        generate_preview_relative_path(".exe")


def test_session_paths_are_generated_from_uuid_and_stay_under_media_root(tmp_path):
    media_root = tmp_path / "media"
    initialize_storage(media_root)
    session_id = "16e169e4-8dda-4b60-9002-b2cbf53e411a"

    chunk_path = generate_session_chunk_path(media_root, session_id, 3)
    assembly_path = generate_session_assembly_path(media_root, session_id)
    staging_path = generate_session_staging_path(media_root, session_id)
    original_path = generate_session_original_relative_path(session_id, "../clip.MOV")

    assert chunk_path == media_root / "tmp/upload-sessions" / session_id / "chunks/3.part"
    assert assembly_path == media_root / "tmp/upload-sessions" / session_id / "assembly.part"
    assert staging_path.parent == media_root / "tmp/upload-staging"
    assert staging_path.name.startswith(f"{session_id}-")
    assert original_path == f"originals/sessions/{session_id}.mov"


def test_session_path_helpers_reject_invalid_session_or_chunk_values(tmp_path):
    media_root = tmp_path / "media"
    initialize_storage(media_root)

    with pytest.raises(StorageError):
        generate_session_chunk_path(media_root, "../escape", 0)
    with pytest.raises(StorageError):
        generate_session_chunk_path(media_root, "16e169e4-8dda-4b60-9002-b2cbf53e411a", -1)


def test_cleanup_session_temporary_files_does_not_remove_originals(tmp_path):
    media_root = tmp_path / "media"
    initialize_storage(media_root)
    session_id = "16e169e4-8dda-4b60-9002-b2cbf53e411a"
    chunk_path = generate_session_chunk_path(media_root, session_id, 0)
    staging_path = generate_session_staging_path(media_root, session_id)
    original_path = media_root / generate_session_original_relative_path(session_id, "clip.mov")
    chunk_path.parent.mkdir(parents=True)
    staging_path.parent.mkdir(parents=True)
    original_path.parent.mkdir(parents=True)
    chunk_path.write_bytes(b"chunk")
    staging_path.write_bytes(b"staging")
    original_path.write_bytes(b"original")

    cleanup_session_temporary_files(media_root, session_id)

    assert not chunk_path.exists()
    assert not staging_path.exists()
    assert original_path.read_bytes() == b"original"
