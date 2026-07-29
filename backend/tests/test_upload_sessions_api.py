from datetime import timedelta
from hashlib import sha256

from fastapi.testclient import TestClient

from app.core.settings import MAX_UPLOAD_CHUNKS, MAX_UPLOAD_SESSION_SIZE_BYTES
from app.db.connection import connect
from app.main import app
from app.repositories.upload_sessions import isoformat, utc_now


def _set_required_env(monkeypatch, tmp_path):
    media_root = tmp_path / "media"
    database_path = tmp_path / "db.sqlite3"
    monkeypatch.setenv("MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    return database_path


def _create(client, client_upload_id="client-upload-123", **overrides):
    body = {
        "client_upload_id": client_upload_id,
        "filename": "clip.mov",
        "size_bytes": 16,
        "expected_file_sha256": "a" * 64,
        "is_log": False,
    }
    body.update(overrides)
    return client.post("/upload-sessions", headers={"Authorization": "Bearer secret-token"}, json=body)


def test_create_session_is_idempotent_and_get_does_not_extend_expiry(monkeypatch, tmp_path):
    database_path = _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("UPLOAD_SESSION_CHUNK_SIZE_BYTES", "8")
    monkeypatch.setenv("UPLOAD_SESSION_MAX_SIZE_BYTES", "1024")

    with TestClient(app) as client:
        created = _create(client)
        session_id = created.json()["id"]
        uploaded = client.put(
            f"/upload-sessions/{session_id}/chunks/0",
            headers={
                "Authorization": "Bearer secret-token",
                "Content-Range": "bytes 0-7/16",
                "X-Chunk-SHA256": sha256(b"12345678").hexdigest(),
            },
            content=b"12345678",
        )
        recovered = _create(client)
        before = connect(database_path, 5000).execute(
            "SELECT expires_at FROM upload_sessions WHERE id = ?", (session_id,)
        ).fetchone()["expires_at"]
        fetched = client.get(f"/upload-sessions/{session_id}", headers={"Authorization": "Bearer secret-token"})
        after = connect(database_path, 5000).execute(
            "SELECT expires_at FROM upload_sessions WHERE id = ?", (session_id,)
        ).fetchone()["expires_at"]

    assert created.status_code == 201
    assert recovered.status_code == 200
    assert recovered.json()["id"] == session_id
    assert created.json()["total_chunks"] == 2
    assert created.json()["expires_at"]
    assert uploaded.status_code == 201
    assert recovered.json()["missing_chunk_indexes"] == [1]
    assert fetched.status_code == 200
    assert fetched.json()["missing_chunk_indexes"] == [1]
    assert before == after


def test_get_expired_session_marks_terminal_and_create_refuses_same_key(monkeypatch, tmp_path):
    database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        created = _create(client)
        session_id = created.json()["id"]
        with connect(database_path, 5000) as conn:
            conn.execute(
                "UPDATE upload_sessions SET expires_at = ? WHERE id = ?",
                (isoformat(utc_now() - timedelta(seconds=1)), session_id),
            )
        expired = client.get(f"/upload-sessions/{session_id}", headers={"Authorization": "Bearer secret-token"})
        retry = _create(client)

    assert expired.status_code == 410
    assert expired.json()["code"] == "session_expired"
    assert retry.status_code == 410
    assert retry.json()["code"] == "session_expired"


def test_session_api_requires_token_and_rejects_size_limit(monkeypatch, tmp_path):
    database_path = _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("UPLOAD_SESSION_MAX_SIZE_BYTES", "8")

    with TestClient(app) as client:
        unauthorized = client.post("/upload-sessions", json={})
        too_large = _create(client)

    assert unauthorized.status_code == 401
    assert too_large.status_code == 413
    assert too_large.json()["code"] == "session_size_limit"
    with connect(database_path, 5000) as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM upload_sessions").fetchone()["count"] == 0


def test_session_api_accepts_exact_hard_size_and_chunk_count_boundary(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        created = _create(
            client,
            client_upload_id="client-upload-hard-boundary",
            size_bytes=MAX_UPLOAD_SESSION_SIZE_BYTES,
        )

    assert created.status_code == 201
    assert created.json()["size_bytes"] == MAX_UPLOAD_SESSION_SIZE_BYTES
    assert created.json()["total_chunks"] == MAX_UPLOAD_CHUNKS
