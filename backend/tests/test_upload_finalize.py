import asyncio
from hashlib import sha256

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.jobs import claim_next_job
from app.schemas.upload_sessions import UploadSessionCreateRequest
from app.services.storage import generate_session_chunk_path, initialize_storage, resolve_media_path
from app.services.upload_finalize import process_upload_finalize_job
from app.services.upload_sessions import (
    create_upload_session,
    finalize_upload_session,
    upload_session_chunk,
)


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


def _prepare_finalization(settings, *, expected_file_sha256=None, is_log=False):
    content = b"12345678abcdefgh"
    session, _ = create_upload_session(
        settings=settings,
        request=UploadSessionCreateRequest(
            client_upload_id="client-upload-123",
            filename="clip.mov",
            size_bytes=len(content),
            expected_file_sha256=expected_file_sha256 or sha256(content).hexdigest(),
            is_log=is_log,
        ),
    )
    for index, chunk in enumerate((content[:8], content[8:])):
        asyncio.run(
            upload_session_chunk(
                settings=settings,
                session_id=session["id"],
                chunk_index=index,
                content_range=(index * 8, index * 8 + 7, len(content)),
                expected_chunk_sha256=sha256(chunk).hexdigest(),
                body_stream=_body(chunk),
            )
        )
    finalizing, job_id = finalize_upload_session(settings=settings, session_id=session["id"])
    with connect(settings.database_path, 5000) as conn:
        job = claim_next_job(conn, settings.job_lease_seconds, {"upload_finalize"})
    assert finalizing["status"] == "assembling"
    assert job is not None and job["id"] == job_id
    return content, session, job


def test_finalization_promotes_verified_original_and_creates_asset_and_preview_job(tmp_path):
    settings = _settings(tmp_path)
    content, session, job = _prepare_finalization(settings)

    processed = process_upload_finalize_job(settings=settings, job=job)

    with connect(settings.database_path, 5000) as conn:
        session_row = conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (session["id"],)).fetchone()
        asset = conn.execute("SELECT * FROM assets").fetchone()
        final_job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        preview_job = conn.execute("SELECT * FROM jobs WHERE job_type = 'preview'").fetchone()

    assert processed is True
    assert session_row["status"] == "completed"
    assert asset["verification_status"] == "file_verified"
    assert asset["server_sha256"] == sha256(content).hexdigest()
    assert final_job["status"] == "done"
    assert preview_job["dedup_key"] == f"initial-preview:{asset['id']}"
    original = resolve_media_path(settings.media_root, asset["original_path"])
    assert original.read_bytes() == content
    assert not generate_session_chunk_path(settings.media_root, session["id"], 0).exists()


def test_finalization_hash_mismatch_is_terminal_without_asset_or_original(tmp_path):
    settings = _settings(tmp_path)
    _content, session, job = _prepare_finalization(settings, expected_file_sha256="a" * 64)

    process_upload_finalize_job(settings=settings, job=job)

    with connect(settings.database_path, 5000) as conn:
        session_row = conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (session["id"],)).fetchone()
        asset_count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()

    assert session_row["status"] == "failed"
    assert session_row["retryable"] == 0
    assert session_row["failure_code"] == "completed_hash_mismatch"
    assert job_row["status"] == "failed"
    assert asset_count == 0
    assert not resolve_media_path(settings.media_root, session["original_relative_path"]).exists()


def test_finalization_recovers_promoted_file_after_database_failure(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    content, session, job = _prepare_finalization(settings)
    from app.services import upload_finalize

    original_insert = upload_finalize.insert_verified_video_asset

    def fail_insert(*args, **kwargs):
        raise OSError("database fault after promotion")

    monkeypatch.setattr(upload_finalize, "insert_verified_video_asset", fail_insert)
    process_upload_finalize_job(settings=settings, job=job)

    final_path = resolve_media_path(settings.media_root, session["original_relative_path"])
    assert final_path.read_bytes() == content
    with connect(settings.database_path, 5000) as conn:
        failed = conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (session["id"],)).fetchone()
    assert failed["status"] == "failed"
    assert failed["retryable"] == 1

    monkeypatch.setattr(upload_finalize, "insert_verified_video_asset", original_insert)
    finalize_upload_session(settings=settings, session_id=session["id"])
    with connect(settings.database_path, 5000) as conn:
        recovered_job = claim_next_job(conn, settings.job_lease_seconds, {"upload_finalize"})
    assert recovered_job is not None

    process_upload_finalize_job(settings=settings, job=recovered_job)

    with connect(settings.database_path, 5000) as conn:
        completed = conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (session["id"],)).fetchone()
        asset = conn.execute("SELECT * FROM assets").fetchone()
    assert completed["status"] == "completed"
    assert asset["server_sha256"] == sha256(content).hexdigest()


def test_finalization_is_idempotent_and_log_uses_existing_lut_safety_job(tmp_path):
    settings = _settings(tmp_path)
    _content, session, job = _prepare_finalization(settings, is_log=True)

    process_upload_finalize_job(settings=settings, job=job)
    process_upload_finalize_job(settings=settings, job=job)

    with connect(settings.database_path, 5000) as conn:
        session_row = conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (session["id"],)).fetchone()
        assets = conn.execute("SELECT * FROM assets").fetchall()
        preview_jobs = conn.execute("SELECT * FROM jobs WHERE job_type = 'lut_preview'").fetchall()

    assert session_row["status"] == "completed"
    assert len(assets) == 1
    assert len(preview_jobs) == 1
    assert preview_jobs[0]["dedup_key"] == f"initial-preview:{assets[0]['id']}"


def test_only_retryable_finalization_failure_can_be_requeued(tmp_path):
    settings = _settings(tmp_path)
    _content, session, job = _prepare_finalization(settings, expected_file_sha256="a" * 64)

    process_upload_finalize_job(settings=settings, job=job)

    from app.services.upload_sessions import UploadSessionServiceError

    try:
        finalize_upload_session(settings=settings, session_id=session["id"])
    except UploadSessionServiceError as error:
        assert error.code == "session_terminal_failure"
    else:
        raise AssertionError("terminal finalization failure must not be requeued")
