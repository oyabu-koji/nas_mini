import hashlib

import pytest
from fastapi.testclient import TestClient

from app.db.connection import connect
from app.main import app
from app.repositories.assets import insert_verified_video_asset, update_preview_status
from app.repositories.derived_files import insert_derived_file
from app.repositories.processed_results import (
    clear_active_processed_result,
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


def _auth_headers():
    return {"Authorization": "Bearer secret-token"}


def _create_deliverable(
    conn,
    media_root,
    *,
    ordinal: int,
    content: bytes = b"0123456789",
    result_id: str | None = None,
):
    result_id = result_id or f"{ordinal:x}" * 32
    asset = insert_verified_video_asset(
        conn,
        filename=f"clip-{ordinal}.mov",
        original_path=f"originals/clip-{ordinal}.mov",
        size_bytes=10,
        server_sha256="a" * 64,
        taken_at=None,
        latitude=None,
        longitude=None,
        exif_json=None,
        is_log=False,
    )
    update_preview_status(conn, asset["id"], "preview_ready")
    relative_path = f"previews/{result_id}.mp4"
    path = media_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
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
        result_id=result_id,
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
            f"session-{ordinal}",
            f"client-{ordinal}",
            f"clip-{ordinal}.mov",
            10,
            "a" * 64,
            10,
            f"originals/clip-{ordinal}.mov",
            "2026-07-18T00:00:00+00:00",
            "2026-07-25T00:00:00+00:00",
            asset["id"],
        ),
    )
    return asset, result, derived, path


def test_processed_result_download_full_range_headers_and_identity(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            with conn:
                asset, result, _derived, _path = _create_deliverable(conn, media_root, ordinal=1)

        response = client.get(
            f"/assets/{asset['id']}/results/{result['id']}",
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    assert response.content == b"0123456789"
    assert response.headers["content-length"] == "10"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["etag"] == f'"{result["sha256"]}"'
    assert response.headers["x-processed-result-id"] == result["id"]
    assert response.headers["x-processed-result-sha256"] == result["sha256"]
    assert response.headers["x-processed-result-size"] == "10"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="processed-{result["id"]}.mp4"'
    )


@pytest.mark.parametrize(
    ("range_header", "expected_body", "expected_range"),
    [
        ("bytes=2-5", b"2345", "bytes 2-5/10"),
        ("bytes=-3", b"789", "bytes 7-9/10"),
        ("bytes=7-", b"789", "bytes 7-9/10"),
    ],
)
def test_processed_result_download_supports_single_ranges(
    monkeypatch,
    tmp_path,
    range_header,
    expected_body,
    expected_range,
):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            with conn:
                asset, result, _derived, _path = _create_deliverable(conn, media_root, ordinal=2)

        response = client.get(
            f"/assets/{asset['id']}/results/{result['id']}",
            headers={**_auth_headers(), "Range": range_header},
        )

    assert response.status_code == 206
    assert response.content == expected_body
    assert response.headers["content-range"] == expected_range
    assert response.headers["content-length"] == str(len(expected_body))


@pytest.mark.parametrize("range_header", ["bytes=0-1,3-4", "items=0-1", "bytes=99-", "bytes=-0"])
def test_processed_result_download_rejects_invalid_or_multi_range(monkeypatch, tmp_path, range_header):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            with conn:
                asset, result, _derived, _path = _create_deliverable(conn, media_root, ordinal=3)

        response = client.get(
            f"/assets/{asset['id']}/results/{result['id']}",
            headers={**_auth_headers(), "Range": range_header},
        )

    assert response.status_code == 416
    assert response.json() == {
        "code": "processed_result_range_not_satisfiable",
        "retryable": False,
    }
    assert response.headers["content-range"] == "bytes */10"


def test_processed_result_download_hides_unknown_cross_asset_and_inactive_result(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            with conn:
                first_asset, first_result, _first_derived, _first_path = _create_deliverable(
                    conn,
                    media_root,
                    ordinal=4,
                )
                second_asset, second_result, _second_derived, _second_path = _create_deliverable(
                    conn,
                    media_root,
                    ordinal=5,
                )
                replacement_content = b"replacement"
                replacement_id = "9" * 32
                (media_root / f"previews/{replacement_id}.mp4").write_bytes(replacement_content)
                replacement_derived = insert_derived_file(
                    conn,
                    asset_id=first_asset["id"],
                    kind="preview",
                    path=f"previews/{replacement_id}.mp4",
                    mime_type="video/mp4",
                    size_bytes=len(replacement_content),
                )
                replacement_result, _created = insert_ready_processed_result(
                    conn,
                    asset_id=first_asset["id"],
                    derived_file_id=replacement_derived["id"],
                    mime_type="video/mp4",
                    size_bytes=len(replacement_content),
                    sha256=hashlib.sha256(replacement_content).hexdigest(),
                    result_id=replacement_id,
                )
                clear_active_processed_result(conn, asset_id=first_asset["id"])
                set_active_processed_result(
                    conn,
                    asset_id=first_asset["id"],
                    result_id=replacement_result["id"],
                )

        cross_asset = client.get(
            f"/assets/{first_asset['id']}/results/{second_result['id']}",
            headers=_auth_headers(),
        )
        unknown = client.get(
            f"/assets/{first_asset['id']}/results/{'f' * 32}",
            headers=_auth_headers(),
        )
        inactive = client.get(
            f"/assets/{first_asset['id']}/results/{first_result['id']}",
            headers=_auth_headers(),
        )

    assert cross_asset.status_code == 404
    assert cross_asset.json()["code"] == "processed_result_not_found"
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "processed_result_not_found"
    assert inactive.status_code == 409
    assert inactive.json()["code"] == "processed_result_superseded"


def test_processed_result_download_returns_not_ready_for_missing_bytes(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            with conn:
                asset, result, _derived, path = _create_deliverable(conn, media_root, ordinal=6)
        path.unlink()

        response = client.get(
            f"/assets/{asset['id']}/results/{result['id']}",
            headers=_auth_headers(),
        )

    assert response.status_code == 409
    assert response.json() == {"code": "processed_result_not_ready", "retryable": False}
    assert str(media_root) not in response.text
    assert 'secret-token' not in response.text
    assert 'previews/' not in response.text


def test_processed_result_descriptor_keeps_requested_bytes_after_pointer_switch(monkeypatch, tmp_path):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            with conn:
                asset, old_result, _old_derived, _old_path = _create_deliverable(
                    conn,
                    media_root,
                    ordinal=7,
                    content=b"old-result",
                )
                new_content = b"new-result"
                new_id = "8" * 32
                new_path = media_root / f"previews/{new_id}.mp4"
                new_path.write_bytes(new_content)
                new_derived = insert_derived_file(
                    conn,
                    asset_id=asset["id"],
                    kind="preview",
                    path=f"previews/{new_id}.mp4",
                    mime_type="video/mp4",
                    size_bytes=len(new_content),
                )
                new_result, _created = insert_ready_processed_result(
                    conn,
                    asset_id=asset["id"],
                    derived_file_id=new_derived["id"],
                    mime_type="video/mp4",
                    size_bytes=len(new_content),
                    sha256=hashlib.sha256(new_content).hexdigest(),
                    result_id=new_id,
                )

        original_iterator = __import__(
            "app.services.processed_result_stream",
            fromlist=["iter_open_file"],
        ).iter_open_file

        def switch_after_open(file, *, start, end):
            with connect(database_path, 5000) as conn:
                with conn:
                    clear_active_processed_result(conn, asset_id=asset["id"])
                    set_active_processed_result(
                        conn,
                        asset_id=asset["id"],
                        result_id=new_result["id"],
                    )
            return original_iterator(file, start=start, end=end)

        monkeypatch.setattr(
            "app.services.processed_result_stream.iter_open_file",
            switch_after_open,
        )
        response = client.get(
            f"/assets/{asset['id']}/results/{old_result['id']}",
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    assert response.content == b"old-result"
    assert response.headers["x-processed-result-id"] == old_result["id"]


def test_processed_result_pointer_switch_before_descriptor_open_returns_superseded(
    monkeypatch,
    tmp_path,
):
    media_root, database_path = _set_required_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            with conn:
                asset, old_result, _old_derived, _old_path = _create_deliverable(
                    conn,
                    media_root,
                    ordinal=9,
                    content=b"old-result",
                )
                new_content = b"new-result"
                new_id = "a" * 32
                (media_root / f"previews/{new_id}.mp4").write_bytes(new_content)
                new_derived = insert_derived_file(
                    conn,
                    asset_id=asset["id"],
                    kind="preview",
                    path=f"previews/{new_id}.mp4",
                    mime_type="video/mp4",
                    size_bytes=len(new_content),
                )
                new_result, _created = insert_ready_processed_result(
                    conn,
                    asset_id=asset["id"],
                    derived_file_id=new_derived["id"],
                    mime_type="video/mp4",
                    size_bytes=len(new_content),
                    sha256=hashlib.sha256(new_content).hexdigest(),
                    result_id=new_id,
                )

        original_parse_range = __import__(
            "app.services.processed_result_stream",
            fromlist=["parse_range_header"],
        ).parse_range_header

        def switch_before_lock(range_header, total_size):
            parsed = original_parse_range(range_header, total_size)
            with connect(database_path, 5000) as conn:
                with conn:
                    clear_active_processed_result(conn, asset_id=asset["id"])
                    set_active_processed_result(
                        conn,
                        asset_id=asset["id"],
                        result_id=new_result["id"],
                    )
            return parsed

        monkeypatch.setattr(
            "app.services.processed_result_stream.parse_range_header",
            switch_before_lock,
        )
        response = client.get(
            f"/assets/{asset['id']}/results/{old_result['id']}",
            headers=_auth_headers(),
        )

    assert response.status_code == 409
    assert response.json() == {"code": "processed_result_superseded", "retryable": False}
