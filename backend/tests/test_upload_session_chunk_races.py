import asyncio
from datetime import timedelta
from hashlib import sha256

import pytest

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.schemas.upload_sessions import UploadSessionCreateRequest
from app.services.storage import generate_session_chunk_path, initialize_storage
from app.services.upload_sessions import (
    UploadSessionServiceError,
    create_upload_session,
    upload_session_chunk,
)
from app.repositories.upload_sessions import isoformat, utc_now


def _settings(tmp_path):
    settings = Settings(
        media_root=tmp_path / "media",
        api_token="secret-token",
        database_path=tmp_path / "db.sqlite3",
        upload_session_chunk_size_bytes=8,
    )
    initialize_storage(settings.media_root)
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)
    return settings


async def _body(content):
    yield content


def _create_session(settings):
    session, _ = create_upload_session(
        settings=settings,
        request=UploadSessionCreateRequest(
            client_upload_id="client-upload-123",
            filename="clip.mov",
            size_bytes=8,
            expected_file_sha256=sha256(b"12345678").hexdigest(),
        ),
    )
    return session


def test_replace_before_database_commit_leaves_no_verified_row_and_next_put_recovers(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    session = _create_session(settings)
    original_record = __import__("app.services.upload_sessions", fromlist=["record_verified_chunk"]).record_verified_chunk

    def fail_after_replace(*args, **kwargs):
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr("app.services.upload_sessions.record_verified_chunk", fail_after_replace)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        asyncio.run(
            upload_session_chunk(
                settings=settings,
                session_id=session["id"],
                chunk_index=0,
                content_range=(0, 7, 8),
                expected_chunk_sha256=sha256(b"12345678").hexdigest(),
                body_stream=_body(b"12345678"),
            )
        )

    canonical = generate_session_chunk_path(settings.media_root, session["id"], 0)
    assert canonical.read_bytes() == b"12345678"
    with connect(settings.database_path, 5000) as conn:
        assert conn.execute("SELECT COUNT(*) FROM upload_chunks").fetchone()[0] == 0

    monkeypatch.setattr("app.services.upload_sessions.record_verified_chunk", original_record)
    asyncio.run(
        upload_session_chunk(
            settings=settings,
            session_id=session["id"],
            chunk_index=0,
            content_range=(0, 7, 8),
            expected_chunk_sha256=sha256(b"12345678").hexdigest(),
            body_stream=_body(b"12345678"),
        )
    )

    with connect(settings.database_path, 5000) as conn:
        assert conn.execute("SELECT COUNT(*) FROM upload_chunks").fetchone()[0] == 1
    assert canonical.read_bytes() == b"12345678"


def test_cancel_while_request_is_staged_prevents_publish(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    session = _create_session(settings)
    release_upload = asyncio.Event()
    staged = asyncio.Event()

    async def delayed_body():
        yield b"12345678"
        staged.set()
        await release_upload.wait()

    async def exercise_race():
        task = asyncio.create_task(
            upload_session_chunk(
                settings=settings,
                session_id=session["id"],
                chunk_index=0,
                content_range=(0, 7, 8),
                expected_chunk_sha256=sha256(b"12345678").hexdigest(),
                body_stream=delayed_body(),
            )
        )
        await staged.wait()
        from app.services.upload_sessions import cancel_upload_session

        cancel_upload_session(settings=settings, session_id=session["id"])
        release_upload.set()
        with pytest.raises(UploadSessionServiceError, match="session_not_uploadable"):
            await task

    asyncio.run(exercise_race())

    canonical = generate_session_chunk_path(settings.media_root, session["id"], 0)
    assert not canonical.exists()
    with connect(settings.database_path, 5000) as conn:
        assert conn.execute("SELECT COUNT(*) FROM upload_chunks").fetchone()[0] == 0


def test_expired_session_status_read_cleans_verified_temporary_chunks(tmp_path):
    settings = _settings(tmp_path)
    session = _create_session(settings)
    asyncio.run(
        upload_session_chunk(
            settings=settings,
            session_id=session["id"],
            chunk_index=0,
            content_range=(0, 7, 8),
            expected_chunk_sha256=sha256(b"12345678").hexdigest(),
            body_stream=_body(b"12345678"),
        )
    )
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "UPDATE upload_sessions SET expires_at = ? WHERE id = ?",
            (isoformat(utc_now() - timedelta(seconds=1)), session["id"]),
        )

    from app.services.upload_sessions import get_upload_session

    with pytest.raises(UploadSessionServiceError, match="session_expired"):
        get_upload_session(settings=settings, session_id=session["id"])

    assert not generate_session_chunk_path(settings.media_root, session["id"], 0).exists()
