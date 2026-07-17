from datetime import timedelta

import pytest

from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.upload_chunks import UploadChunkError, record_verified_chunk
from app.repositories.upload_sessions import (
    UploadSessionError,
    create_or_return_session,
    get_session_or_expire,
    isoformat,
    mark_cancelled,
    utc_now,
)


def _session_data(identifier: str, now, **overrides):
    data = {
        "id": identifier,
        "client_upload_id": f"client-{identifier}",
        "filename": "clip.mov",
        "size_bytes": 16,
        "expected_file_sha256": "a" * 64,
        "chunk_size_bytes": 8,
        "original_relative_path": f"originals/sessions/{identifier}.mov",
        "taken_at": None,
        "latitude": None,
        "longitude": None,
        "exif_json": None,
        "is_log": False,
        "expires_at": isoformat(now + timedelta(days=7)),
    }
    data.update(overrides)
    return data


def test_existing_session_is_returned_before_active_limit_and_immutable_conflicts_fail(tmp_path):
    database_path = tmp_path / "db.sqlite3"
    now = utc_now()
    first = _session_data("16e169e4-8dda-4b60-9002-b2cbf53e411a", now)

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        created, did_create = create_or_return_session(conn, session=first, active_limit=1, now=now)
        recovered, did_recover = create_or_return_session(conn, session=first, active_limit=1, now=now)
        with pytest.raises(UploadSessionError, match="session_metadata_conflict"):
            create_or_return_session(
                conn,
                session=_session_data(first["id"], now, filename="other.mov"),
                active_limit=1,
                now=now,
            )

    assert did_create is True
    assert did_recover is False
    assert recovered["id"] == created["id"]


def test_new_session_limit_expiry_and_cancelled_key_are_terminal(tmp_path):
    database_path = tmp_path / "db.sqlite3"
    now = utc_now()
    first = _session_data("16e169e4-8dda-4b60-9002-b2cbf53e411a", now)
    second = _session_data("4f70ed6e-5c47-4cdb-a600-1e3db08b8ca0", now)

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        create_or_return_session(conn, session=first, active_limit=1, now=now)
        with pytest.raises(UploadSessionError, match="active_session_limit"):
            create_or_return_session(conn, session=second, active_limit=1, now=now)
        cancelled = mark_cancelled(conn, first["id"], now=now)
        with pytest.raises(UploadSessionError, match="session_cancelled"):
            create_or_return_session(conn, session=first, active_limit=1, now=now)
        create_or_return_session(conn, session=second, active_limit=1, now=now)
        expired = get_session_or_expire(conn, second["id"], now=now + timedelta(days=8))

    assert cancelled["status"] == "cancelled"
    assert expired["status"] == "expired"


def test_verified_chunk_is_idempotent_only_when_range_and_hash_match(tmp_path):
    database_path = tmp_path / "db.sqlite3"
    now = utc_now()
    session = _session_data("16e169e4-8dda-4b60-9002-b2cbf53e411a", now)

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        create_or_return_session(conn, session=session, active_limit=2, now=now)
        chunk, inserted = record_verified_chunk(
            conn,
            session_id=session["id"],
            chunk_index=0,
            start_offset=0,
            end_offset=7,
            size_bytes=8,
            sha256="b" * 64,
        )
        recovered, inserted_again = record_verified_chunk(
            conn,
            session_id=session["id"],
            chunk_index=0,
            start_offset=0,
            end_offset=7,
            size_bytes=8,
            sha256="b" * 64,
        )
        with pytest.raises(UploadChunkError, match="chunk_conflict"):
            record_verified_chunk(
                conn,
                session_id=session["id"],
                chunk_index=0,
                start_offset=0,
                end_offset=7,
                size_bytes=8,
                sha256="c" * 64,
            )

    assert inserted is True
    assert inserted_again is False
    assert recovered["id"] == chunk["id"]
