import json
import hashlib

import pytest
from fastapi.testclient import TestClient

from app.db.connection import connect
from app.main import app
from app.repositories.assets import insert_asset, insert_verified_video_asset, update_preview_status
from app.repositories.derived_files import insert_derived_file
from app.repositories.processed_results import (
    insert_ready_processed_result,
    set_active_processed_result,
)


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


def _insert_asset(conn, *, filename="clip.mov"):
    return insert_asset(
        conn,
        type="video",
        filename=filename,
        original_path=f"originals/{filename}",
        size_bytes=10,
        server_sha256="abc123",
        taken_at="2026-06-25T10:00:00Z",
        latitude=35.0,
        longitude=139.0,
        exif_json=json.dumps({"camera": "iPhone"}, separators=(",", ":")),
        is_log=False,
    )


def _insert_deliverable_phase2a_asset(conn, media_root):
    content = b"processed-result"
    asset = insert_verified_video_asset(
        conn,
        filename="phase2a.mov",
        original_path="originals/phase2a.mov",
        size_bytes=10,
        server_sha256="a" * 64,
        taken_at=None,
        latitude=None,
        longitude=None,
        exif_json=None,
        is_log=False,
    )
    update_preview_status(conn, asset["id"], "preview_ready")
    relative_path = "previews/" + "a" * 32 + ".mp4"
    (media_root / relative_path).parent.mkdir(parents=True, exist_ok=True)
    (media_root / relative_path).write_bytes(content)
    derived = insert_derived_file(
        conn,
        asset_id=asset["id"],
        kind="preview",
        path=relative_path,
        mime_type="video/mp4",
        size_bytes=len(content),
    )
    result, _created = insert_ready_processed_result(
        conn,
        asset_id=asset["id"],
        derived_file_id=derived["id"],
        mime_type="video/mp4",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        result_id="b" * 32,
    )
    set_active_processed_result(conn, asset_id=asset["id"], result_id=result["id"])
    conn.execute(
        """
        INSERT INTO upload_sessions (
            id, client_upload_id, type, filename, size_bytes,
            expected_file_sha256, chunk_size_bytes, original_relative_path,
            status, last_activity_at, expires_at, asset_id
        ) VALUES (?, ?, 'video', ?, ?, ?, ?, ?, 'completed', ?, ?, ?)
        """,
        (
            "phase2a-session",
            "phase2a-client",
            "phase2a.mov",
            10,
            "a" * 64,
            10,
            "originals/phase2a.mov",
            "2026-07-18T00:00:00+00:00",
            "2026-07-25T00:00:00+00:00",
            asset["id"],
        ),
    )
    return asset, result, relative_path


def test_asset_list_requires_authentication(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.get("/assets")

    assert response.status_code == 401


@pytest.mark.parametrize("headers", _auth_cases())
def test_asset_list_rejects_invalid_authentication(monkeypatch, tmp_path, headers):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.get("/assets", headers=headers)

    assert response.status_code == 401
    assert "wrong-token" not in response.text


@pytest.mark.parametrize("headers", _auth_cases())
def test_asset_detail_rejects_invalid_authentication(monkeypatch, tmp_path, headers):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.get("/assets/1", headers=headers)

    assert response.status_code == 401
    assert "wrong-token" not in response.text


def test_asset_list_returns_pagination_order_and_preview_metadata(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            first = _insert_asset(conn, filename="first.mov")
            second = _insert_asset(conn, filename="second.mov")
            update_preview_status(conn, second["id"], "preview_ready")
            insert_derived_file(
                conn,
                asset_id=second["id"],
                kind="preview",
                path="previews/second.mp4",
                mime_type="video/mp4",
                size_bytes=123,
            )

        response = client.get("/assets?limit=10&offset=0", headers=_auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 10
    assert body["offset"] == 0
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [second["id"], first["id"]]
    assert body["items"][0]["preview"]["url"] == f"/assets/{second['id']}/preview"
    assert body["items"][0]["preview"]["mime_type"] == "video/mp4"
    assert body["items"][1]["preview"] is None
    assert body["items"][0]["exif_json"] == {"camera": "iPhone"}
    assert "original_path" not in body["items"][0]
    assert "local_delete_status" not in body["items"][0]
    assert "previews/second.mp4" not in response.text
    assert str(media_root) not in response.text
    assert "active_processed_result" not in body["items"][0]


def test_asset_list_validates_pagination(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        too_large = client.get("/assets?limit=101", headers=_auth_headers())
        negative_offset = client.get("/assets?offset=-1", headers=_auth_headers())

    assert too_large.status_code == 422
    assert negative_offset.status_code == 422


def test_asset_detail_returns_asset_and_preview(monkeypatch, tmp_path):
    _media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = _insert_asset(conn)
            insert_derived_file(
                conn,
                asset_id=asset["id"],
                kind="preview",
                path="previews/clip.mp4",
                mime_type="video/mp4",
                size_bytes=123,
            )

        response = client.get(f"/assets/{asset['id']}", headers=_auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == asset["id"]
    assert body["filename"] == "clip.mov"
    assert body["preview"]["url"] == f"/assets/{asset['id']}/preview"
    assert "original_path" not in body
    assert "previews/clip.mp4" not in response.text


def test_asset_detail_missing_returns_404(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.get("/assets/999", headers=_auth_headers())

    assert response.status_code == 404


def test_asset_detail_returns_only_verified_active_processed_result(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            with conn:
                asset, result, relative_path = _insert_deliverable_phase2a_asset(conn, media_root)

        detail = client.get(f"/assets/{asset['id']}", headers=_auth_headers())
        listing = client.get("/assets", headers=_auth_headers())
        (media_root / relative_path).unlink()
        missing_file_detail = client.get(f"/assets/{asset['id']}", headers=_auth_headers())

    assert detail.status_code == 200
    active = detail.json()["active_processed_result"]
    assert active["result_id"] == result["id"]
    assert active["url"] == f"/assets/{asset['id']}/results/{result['id']}"
    assert active["sha256"] == result["sha256"]
    assert "id" not in active
    assert "active_processed_result" not in listing.json()["items"][0]
    assert missing_file_detail.status_code == 200
    assert missing_file_detail.json()["active_processed_result"] is None
    assert relative_path not in missing_file_detail.text
