from hashlib import sha256

from fastapi.testclient import TestClient

from app.db.connection import connect
from app.main import app


def _set_required_env(monkeypatch, tmp_path):
    media_root = tmp_path / "media"
    database_path = tmp_path / "db.sqlite3"
    monkeypatch.setenv("MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("UPLOAD_SESSION_CHUNK_SIZE_BYTES", "8")
    return media_root, database_path


def _create(client):
    response = client.post(
        "/upload-sessions",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "client_upload_id": "client-upload-123",
            "filename": "clip.mov",
            "size_bytes": 16,
            "expected_file_sha256": "a" * 64,
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def _put(client, session_id, index, content, content_range):
    return client.put(
        f"/upload-sessions/{session_id}/chunks/{index}",
        headers={
            "Authorization": "Bearer secret-token",
            "Content-Range": content_range,
            "X-Chunk-SHA256": sha256(content).hexdigest(),
        },
        content=content,
    )


def test_chunk_put_publishes_verified_bytes_and_same_retry_is_idempotent(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)
    content = b"12345678"

    with TestClient(app) as client:
        session_id = _create(client)
        first = _put(client, session_id, 0, content, "bytes 0-7/16")
        retry = _put(client, session_id, 0, content, "bytes 0-7/16")

    assert first.status_code == 201
    assert first.json()["idempotent"] is False
    assert retry.status_code == 200
    assert retry.json()["idempotent"] is True
    with connect(database_path, 5000) as conn:
        chunk = conn.execute("SELECT * FROM upload_chunks").fetchone()
    assert chunk["sha256"] == sha256(content).hexdigest()
    canonical = media_root / "tmp/upload-sessions" / session_id / "chunks/0.part"
    assert canonical.read_bytes() == content


def test_chunk_put_rejects_wrong_range_and_conflicting_verified_bytes(monkeypatch, tmp_path):
    _media_root, _database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        session_id = _create(client)
        invalid = _put(client, session_id, 0, b"12345678", "bytes 0-7/15")
        accepted = _put(client, session_id, 0, b"12345678", "bytes 0-7/16")
        conflict = _put(client, session_id, 0, b"abcdefgh", "bytes 0-7/16")

    assert invalid.status_code == 409
    assert invalid.json()["code"] == "chunk_range_invalid"
    assert accepted.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "chunk_conflict"


def test_chunk_put_rejects_missing_headers_and_token(monkeypatch, tmp_path):
    _media_root, _database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        session_id = _create(client)
        missing_headers = client.put(
            f"/upload-sessions/{session_id}/chunks/0",
            headers={"Authorization": "Bearer secret-token"},
            content=b"12345678",
        )
        no_token = client.put(f"/upload-sessions/{session_id}/chunks/0", content=b"12345678")

    assert missing_headers.status_code == 422
    assert missing_headers.json()["code"] == "chunk_request_invalid"
    assert no_token.status_code == 401
