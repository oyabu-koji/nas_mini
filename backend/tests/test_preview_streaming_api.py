import pytest
from fastapi.testclient import TestClient

from app.db.connection import connect
from app.main import app
from app.repositories.assets import insert_asset, update_preview_status
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


def _ready_preview(conn, media_root, *, content=b"0123456789", path="previews/clip.mp4", mime_type="video/mp4"):
    asset = _insert_asset(conn)
    update_preview_status(conn, asset["id"], "preview_ready")
    preview_path = media_root / path
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_bytes(content)
    insert_derived_file(
        conn,
        asset_id=asset["id"],
        kind="preview",
        path=path,
        mime_type=mime_type,
        size_bytes=len(content),
    )
    return asset


def test_preview_stream_requires_authentication(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.get("/assets/1/preview")

    assert response.status_code == 401


@pytest.mark.parametrize("headers", _auth_cases())
def test_preview_stream_rejects_invalid_authentication(monkeypatch, tmp_path, headers):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.get("/assets/1/preview", headers=headers)

    assert response.status_code == 401
    assert "wrong-token" not in response.text


def test_preview_stream_full_response(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = _ready_preview(conn, media_root)

        response = client.get(f"/assets/{asset['id']}/preview", headers=_auth_headers())

    assert response.status_code == 200
    assert response.content == b"0123456789"
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.headers["content-length"] == "10"
    assert response.headers["accept-ranges"] == "bytes"


def test_preview_stream_supports_start_end_range(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = _ready_preview(conn, media_root)

        response = client.get(
            f"/assets/{asset['id']}/preview",
            headers={**_auth_headers(), "Range": "bytes=2-5"},
        )

    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["content-length"] == "4"
    assert response.headers["accept-ranges"] == "bytes"


def test_preview_stream_supports_start_to_end_range(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = _ready_preview(conn, media_root)

        response = client.get(
            f"/assets/{asset['id']}/preview",
            headers={**_auth_headers(), "Range": "bytes=7-"},
        )

    assert response.status_code == 206
    assert response.content == b"789"
    assert response.headers["content-range"] == "bytes 7-9/10"


def test_preview_stream_supports_suffix_range(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = _ready_preview(conn, media_root)

        response = client.get(
            f"/assets/{asset['id']}/preview",
            headers={**_auth_headers(), "Range": "bytes=-3"},
        )

    assert response.status_code == 206
    assert response.content == b"789"
    assert response.headers["content-range"] == "bytes 7-9/10"


def test_preview_stream_rejects_invalid_and_multi_range(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = _ready_preview(conn, media_root)

        invalid = client.get(
            f"/assets/{asset['id']}/preview",
            headers={**_auth_headers(), "Range": "bytes=99-"},
        )
        multi = client.get(
            f"/assets/{asset['id']}/preview",
            headers={**_auth_headers(), "Range": "bytes=0-1,3-4"},
        )

    assert invalid.status_code == 416
    assert invalid.headers["content-range"] == "bytes */10"
    assert multi.status_code == 416
    assert multi.headers["content-range"] == "bytes */10"


def test_preview_stream_missing_asset_and_not_ready(monkeypatch, tmp_path):
    _media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = _insert_asset(conn)

        missing = client.get("/assets/999/preview", headers=_auth_headers())
        not_ready = client.get(f"/assets/{asset['id']}/preview", headers=_auth_headers())

    assert missing.status_code == 404
    assert not_ready.status_code == 409


def test_preview_stream_missing_preview_record(monkeypatch, tmp_path):
    _media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = _insert_asset(conn)
            update_preview_status(conn, asset["id"], "preview_ready")

        response = client.get(f"/assets/{asset['id']}/preview", headers=_auth_headers())

    assert response.status_code == 404


def test_preview_stream_storage_failures_are_sanitized(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)
    host_path = media_root / "previews" / "missing.mp4"

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = _insert_asset(conn)
            update_preview_status(conn, asset["id"], "preview_ready")
            insert_derived_file(
                conn,
                asset_id=asset["id"],
                kind="preview",
                path="previews/missing.mp4",
                mime_type="video/mp4",
                size_bytes=10,
            )

        response = client.get(f"/assets/{asset['id']}/preview", headers=_auth_headers())

    assert response.status_code == 500
    assert str(host_path) not in response.text
    assert "Preview storage failure" in response.text


def test_preview_stream_missing_mime_type_is_storage_failure(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = _ready_preview(conn, media_root, mime_type=None)

        response = client.get(f"/assets/{asset['id']}/preview", headers=_auth_headers())

    assert response.status_code == 500
