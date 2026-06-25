import pytest
from fastapi.testclient import TestClient

from app.db.connection import connect
from app.main import app
from app.repositories.assets import get_asset, insert_asset, update_preview_status
from app.repositories.derived_files import insert_derived_file


def _set_required_env(monkeypatch, tmp_path):
    media_root = tmp_path / "media"
    database_path = tmp_path / "db.sqlite3"
    monkeypatch.setenv("MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    return media_root, database_path


def _auth_headers(token: str = "secret-token"):
    return {"Authorization": f"Bearer {token}"}


def _auth_cases():
    return [
        pytest.param(None, id="missing"),
        pytest.param({"Authorization": "Token secret-token"}, id="malformed"),
        pytest.param(_auth_headers("wrong-token"), id="wrong-token"),
    ]


def _insert_asset(conn):
    return insert_asset(
        conn,
        type="video",
        filename="clip.mov",
        original_path="originals/clip.mov",
        size_bytes=10,
        server_sha256="abc123",
        taken_at=None,
        latitude=None,
        longitude=None,
        exif_json=None,
        is_log=False,
    )


def _ready_preview(conn, media_root, *, path="previews/clip.mp4", mime_type="video/mp4", write_file=True):
    asset = _insert_asset(conn)
    update_preview_status(conn, asset["id"], "preview_ready")
    if write_file:
        preview_path = media_root / path
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_bytes(b"preview")
    insert_derived_file(
        conn,
        asset_id=asset["id"],
        kind="preview",
        path=path,
        mime_type=mime_type,
        size_bytes=7,
    )
    return asset


def test_preview_confirmation_requires_authentication(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.post("/assets/1/preview-confirmation")

    assert response.status_code == 401


@pytest.mark.parametrize("headers", _auth_cases())
def test_preview_confirmation_rejects_invalid_authentication(monkeypatch, tmp_path, headers):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.post("/assets/1/preview-confirmation", headers=headers)

    assert response.status_code == 401
    assert "wrong-token" not in response.text


def test_preview_confirmation_success_and_idempotency(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = _ready_preview(conn, media_root)

        first = client.post(
            f"/assets/{asset['id']}/preview-confirmation",
            headers=_auth_headers(),
        )
        second = client.post(
            f"/assets/{asset['id']}/preview-confirmation",
            headers=_auth_headers(),
        )
        with connect(database_path, 5000) as conn:
            row = get_asset(conn, asset["id"])

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["review_status"] == "preview_confirmed"
    assert row is not None
    assert row["review_status"] == "preview_confirmed"
    assert row["preview_status"] == "preview_ready"
    assert row["verification_status"] == "server_hash_recorded"
    assert row["delete_candidate_status"] == "not_candidate"
    body = first.json()
    assert "original_path" not in body
    assert "local_delete_status" not in body
    assert "path" not in body["preview"]
    assert "previews/clip.mp4" not in first.text
    assert str(media_root) not in first.text


def test_preview_confirmation_missing_asset_and_not_ready(monkeypatch, tmp_path):
    _media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = _insert_asset(conn)

        missing = client.post("/assets/999/preview-confirmation", headers=_auth_headers())
        not_ready = client.post(
            f"/assets/{asset['id']}/preview-confirmation",
            headers=_auth_headers(),
        )

    assert missing.status_code == 404
    assert not_ready.status_code == 409


def test_preview_confirmation_requires_preview_record(monkeypatch, tmp_path):
    _media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = _insert_asset(conn)
            update_preview_status(conn, asset["id"], "preview_ready")

        response = client.post(
            f"/assets/{asset['id']}/preview-confirmation",
            headers=_auth_headers(),
        )

    assert response.status_code == 409


def test_preview_confirmation_rejects_missing_file_unsafe_path_and_missing_mime(
    monkeypatch,
    tmp_path,
):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            missing_file = _ready_preview(conn, media_root, path="previews/missing.mp4", write_file=False)
            unsafe = _ready_preview(conn, media_root, path="../outside.mp4", write_file=False)
            missing_mime = _ready_preview(conn, media_root, path="previews/no-mime.mp4", mime_type=None)

        missing_file_response = client.post(
            f"/assets/{missing_file['id']}/preview-confirmation",
            headers=_auth_headers(),
        )
        unsafe_response = client.post(
            f"/assets/{unsafe['id']}/preview-confirmation",
            headers=_auth_headers(),
        )
        missing_mime_response = client.post(
            f"/assets/{missing_mime['id']}/preview-confirmation",
            headers=_auth_headers(),
        )

    assert missing_file_response.status_code == 409
    assert unsafe_response.status_code == 409
    assert missing_mime_response.status_code == 409
