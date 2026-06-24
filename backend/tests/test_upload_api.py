import json
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
    return media_root, database_path


def _upload(
    client: TestClient,
    *,
    token: str = "secret-token",
    data: dict[str, str] | None = None,
    content: bytes = b"media-content",
):
    fields = {
        "type": "image",
        "filename": "../client-name.JPG",
        "taken_at": "",
        "latitude": "",
        "longitude": "",
        "exif_json": '{"camera":"iPhone"}',
        "is_log": "false",
    }
    if data:
        fields.update(data)

    return client.post(
        "/assets/upload",
        headers={"Authorization": f"Bearer {token}"},
        data=fields,
        files={"file": ("client-name.JPG", content, "image/jpeg")},
    )


def test_upload_rejects_missing_token(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/assets/upload",
            data={"type": "image", "filename": "photo.jpg"},
            files={"file": ("photo.jpg", b"content", "image/jpeg")},
        )

    assert response.status_code == 401


def test_upload_rejects_invalid_token(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = _upload(client, token="wrong-token")

    assert response.status_code == 401
    assert "wrong-token" not in response.text


def test_upload_accepts_image_and_creates_asset_and_preview_job(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)
    content = b"image-bytes"

    with TestClient(app) as client:
        response = _upload(client, content=content)

    assert response.status_code == 201
    body = response.json()
    asset = body["asset"]
    job = body["job"]

    assert asset["type"] == "image"
    assert asset["filename"] == "../client-name.JPG"
    assert asset["original_path"].startswith("originals/")
    assert "../client-name" not in asset["original_path"]
    assert str(media_root) not in response.text
    assert asset["server_sha256"] == sha256(content).hexdigest()
    assert body["server_sha256"] == asset["server_sha256"]
    assert body["transfer_status"] == "uploaded"
    assert body["verification_status"] == "server_hash_recorded"
    assert body["preview_status"] == "preview_generating"
    assert body["review_status"] == "not_reviewed"
    assert body["delete_candidate_status"] == "not_candidate"
    assert job["job_type"] == "preview"
    assert job["status"] == "queued"

    original_path = media_root / asset["original_path"]
    assert original_path.read_bytes() == content

    with connect(database_path, 5000) as conn:
        asset_row = conn.execute("SELECT * FROM assets").fetchone()
        job_row = conn.execute("SELECT * FROM jobs").fetchone()

    assert asset_row["original_path"] == asset["original_path"]
    assert asset_row["server_sha256"] == sha256(content).hexdigest()
    payload = json.loads(job_row["payload_json"])
    assert payload == {
        "asset_id": asset["id"],
        "original_path": asset["original_path"],
        "type": "image",
        "is_log": False,
    }


def test_upload_accepts_video_log_and_creates_lut_preview_job(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = _upload(
            client,
            data={"type": "video", "filename": "clip.mov", "is_log": "true"},
            content=b"video-bytes",
        )

    assert response.status_code == 201
    body = response.json()
    assert body["asset"]["type"] == "video"
    assert body["asset"]["is_log"] is True
    assert body["job"]["job_type"] == "lut_preview"


def test_upload_rejects_invalid_type(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = _upload(client, data={"type": "audio"})

    assert response.status_code == 422


def test_upload_rejects_invalid_exif_json(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = _upload(client, data={"exif_json": "{not-json"})

    assert response.status_code == 422


def test_upload_rejects_too_large_file_and_cleans_tmp(monkeypatch, tmp_path):
    media_root, _database_path = _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setattr("app.services.upload.MAX_UPLOAD_SIZE_BYTES", 3)

    with TestClient(app) as client:
        response = _upload(client, content=b"too-large")

    assert response.status_code == 413
    assert list((media_root / "tmp").iterdir()) == []
    assert list((media_root / "originals").iterdir()) == []
