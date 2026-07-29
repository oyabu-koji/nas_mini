from hashlib import sha256

from fastapi.testclient import TestClient

from app.db.connection import connect
from app.main import app
from app.repositories.jobs import insert_or_return_job


def _set_required_env(monkeypatch, tmp_path):
    media_root = tmp_path / "media"
    database_path = tmp_path / "db.sqlite3"
    monkeypatch.setenv("MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("UPLOAD_SESSION_CHUNK_SIZE_BYTES", "8")
    monkeypatch.setenv("UPLOAD_SESSION_MAX_SIZE_BYTES", "1024")
    return media_root, database_path


def _create(client):
    content = b"12345678abcdefgh"
    response = client.post(
        "/upload-sessions",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "client_upload_id": "client-upload-123",
            "filename": "clip.mov",
            "size_bytes": len(content),
            "expected_file_sha256": sha256(content).hexdigest(),
        },
    )
    assert response.status_code == 201
    return response.json()["id"], content


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


def test_finalize_requires_all_chunks_then_queues_one_deduplicated_job(monkeypatch, tmp_path):
    _media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        session_id, content = _create(client)
        incomplete = client.post(f"/upload-sessions/{session_id}/finalize", headers={"Authorization": "Bearer secret-token"})
        _put(client, session_id, 0, content[:8], "bytes 0-7/16")
        _put(client, session_id, 1, content[8:], "bytes 8-15/16")
        finalized = client.post(f"/upload-sessions/{session_id}/finalize", headers={"Authorization": "Bearer secret-token"})
        retry = client.post(f"/upload-sessions/{session_id}/finalize", headers={"Authorization": "Bearer secret-token"})

    assert incomplete.status_code == 409
    assert incomplete.json()["code"] == "missing_chunks"
    assert finalized.status_code == 202
    assert finalized.json()["session"]["status"] == "assembling"
    assert retry.status_code == 202
    assert retry.json()["job_id"] == finalized.json()["job_id"]
    with connect(database_path, 5000) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE job_type = 'upload_finalize'").fetchone()[0] == 1


def test_cancel_removes_temporary_chunks_and_is_terminal(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        session_id, content = _create(client)
        _put(client, session_id, 0, content[:8], "bytes 0-7/16")
        cancelled = client.delete(f"/upload-sessions/{session_id}", headers={"Authorization": "Bearer secret-token"})
        retry = client.post(
            "/upload-sessions",
            headers={"Authorization": "Bearer secret-token"},
            json={
                "client_upload_id": "client-upload-123",
                "filename": "clip.mov",
                "size_bytes": len(content),
                "expected_file_sha256": sha256(content).hexdigest(),
            },
        )

    assert cancelled.status_code == 204
    assert not cancelled.content
    assert not (media_root / "tmp/upload-sessions" / session_id).exists()
    assert retry.status_code == 409
    assert retry.json()["code"] == "session_cancelled"
    with connect(database_path, 5000) as conn:
        assert conn.execute("SELECT status FROM upload_sessions").fetchone()["status"] == "cancelled"


def test_finalize_returns_existing_asset_and_preview_job_after_completion(monkeypatch, tmp_path):
    _media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        session_id, _content = _create(client)
        with connect(database_path, 5000) as conn:
            cursor = conn.execute(
                """
                INSERT INTO assets (type, filename, original_path, verification_status)
                VALUES ('video', 'clip.mov', 'originals/verified.mov', 'file_verified')
                """
            )
            asset_id = cursor.lastrowid
            preview_job, _ = insert_or_return_job(
                conn,
                job_type="preview",
                asset_id=asset_id,
                payload_json="{}",
                dedup_key=f"initial-preview:{asset_id}",
            )
            conn.execute(
                "UPDATE upload_sessions SET status = 'completed', asset_id = ? WHERE id = ?",
                (asset_id, session_id),
            )

        completed = client.post(f"/upload-sessions/{session_id}/finalize", headers={"Authorization": "Bearer secret-token"})

    assert completed.status_code == 200
    assert completed.json()["asset_id"] == asset_id
    assert completed.json()["preview_job_id"] == preview_job["id"]
    assert completed.json()["session"]["asset_id"] == asset_id
