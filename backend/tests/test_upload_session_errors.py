from fastapi.testclient import TestClient

from app.main import app


def _set_required_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("UPLOAD_SESSION_ACTIVE_LIMIT", "1")
    monkeypatch.setenv("UPLOAD_SESSION_RETRY_AFTER_SECONDS", "17")


def _session_payload(client_upload_id):
    return {
        "client_upload_id": client_upload_id,
        "filename": "clip.mov",
        "size_bytes": 8,
        "expected_file_sha256": "a" * 64,
    }


def test_session_errors_use_stable_codes_without_reflecting_request_data(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)
    secret_filename = "private-client-path-and-token.mov"

    with TestClient(app) as client:
        malformed = client.post(
            "/upload-sessions",
            headers={"Authorization": "Bearer secret-token"},
            json={**_session_payload("bad id"), "filename": secret_filename},
        )
        first = client.post(
            "/upload-sessions",
            headers={"Authorization": "Bearer secret-token"},
            json=_session_payload("client-upload-123"),
        )
        limited = client.post(
            "/upload-sessions",
            headers={"Authorization": "Bearer secret-token"},
            json=_session_payload("client-upload-456"),
        )

    assert malformed.status_code == 422
    assert malformed.json() == {"code": "validation_error", "retryable": False}
    assert secret_filename not in malformed.text
    assert first.status_code == 201
    assert limited.status_code == 429
    assert limited.json() == {
        "code": "active_session_limit",
        "retryable": True,
        "retry_after_seconds": 17,
    }
