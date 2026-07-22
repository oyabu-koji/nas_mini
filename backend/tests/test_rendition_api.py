import hashlib

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.db.connection import connect
from app.main import app
from app.repositories.assets import insert_verified_video_asset, update_preview_status
from app.repositories.derived_files import insert_derived_file
from app.repositories.processed_results import (
    insert_ready_processed_result,
    set_active_processed_result,
)
from app.services.rendition_creation import RenditionCreationError, create_rendition


def configure(monkeypatch, tmp_path):
    media_root = tmp_path / "media"
    database_path = tmp_path / "db.sqlite3"
    monkeypatch.setenv("MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    return media_root, database_path


def auth(token="secret-token"):
    return {"Authorization": f"Bearer {token}"}


def seed_eligible_asset(conn, media_root, *, suffix="a"):
    original = f"original-{suffix}".encode()
    original_relative = f"originals/sessions/{suffix}.mov"
    original_path = media_root / original_relative
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(original)
    asset = insert_verified_video_asset(
        conn,
        filename=f"{suffix}.mov",
        original_path=original_relative,
        size_bytes=len(original),
        server_sha256=hashlib.sha256(original).hexdigest(),
        taken_at=None,
        latitude=None,
        longitude=None,
        exif_json=None,
        is_log=False,
    )
    update_preview_status(conn, asset["id"], "preview_ready")
    preview = f"preview-{suffix}".encode()
    preview_relative = f"previews/{suffix * 32}.mp4"
    preview_path = media_root / preview_relative
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_bytes(preview)
    derived = insert_derived_file(
        conn,
        asset_id=asset["id"],
        kind="preview",
        path=preview_relative,
        mime_type="video/mp4",
        size_bytes=len(preview),
    )
    result, _ = insert_ready_processed_result(
        conn,
        asset_id=asset["id"],
        derived_file_id=derived["id"],
        mime_type="video/mp4",
        size_bytes=len(preview),
        sha256=hashlib.sha256(preview).hexdigest(),
        result_id=suffix * 32,
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
            f"session-{suffix}",
            f"client-{suffix}",
            f"{suffix}.mov",
            len(original),
            hashlib.sha256(original).hexdigest(),
            8,
            original_relative,
            "2026-07-21T00:00:00+00:00",
            "2027-07-21T00:00:00+00:00",
            asset["id"],
        ),
    )
    return asset


def request_body(client_id="c" * 32, preset_id="compress-only"):
    return {"client_rendition_request_id": client_id, "preset_id": preset_id}


def test_create_and_poll_rendition_with_exact_replay(monkeypatch, tmp_path):
    media_root, database_path = configure(monkeypatch, tmp_path)
    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = seed_eligible_asset(conn, media_root)
            conn.commit()

        created = client.post(
            f"/api/v1/assets/{asset['id']}/renditions",
            headers=auth(),
            json=request_body(),
        )
        replayed = client.post(
            f"/api/v1/assets/{asset['id']}/renditions",
            headers=auth(),
            json=request_body(),
        )
        polled = client.get(
            f"/api/v1/assets/{asset['id']}/renditions/{created.json()['rendition_id']}",
            headers=auth(),
        )

    assert created.status_code == 202
    assert replayed.status_code == 200
    assert replayed.json() == created.json()
    assert polled.status_code == 200
    assert polled.json()["state"] == "queued"
    assert polled.json()["applied_preset_id"] is None
    with connect(database_path, 5000) as conn:
        assert conn.execute("SELECT COUNT(*) FROM renditions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE job_type = 'rendition'").fetchone()[0] == 1


def test_replay_is_resolved_before_current_eligibility_and_conflict_is_global(monkeypatch, tmp_path):
    media_root, database_path = configure(monkeypatch, tmp_path)
    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = seed_eligible_asset(conn, media_root)
            conn.commit()
        created = client.post(
            f"/api/v1/assets/{asset['id']}/renditions", headers=auth(), json=request_body()
        )
        with connect(database_path, 5000) as conn:
            conn.execute("UPDATE assets SET is_log = 1 WHERE id = ?", (asset["id"],))
            conn.commit()
        replayed = client.post(
            f"/api/v1/assets/{asset['id']}/renditions", headers=auth(), json=request_body()
        )
        conflict = client.post(
            f"/api/v1/assets/{asset['id']}/renditions",
            headers=auth(),
            json=request_body(preset_id="identity-v1"),
        )

    assert created.status_code == 202
    assert replayed.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json() == {"code": "rendition_request_conflict", "retryable": False}


@pytest.mark.parametrize(
    "mutation",
    [
        "image",
        "non_session",
        "non_verified",
        "non_ready",
        "missing_active",
        "legacy_log",
        "corrupt_result",
    ],
)
def test_ineligible_asset_creates_no_rendition_state(monkeypatch, tmp_path, mutation):
    media_root, database_path = configure(monkeypatch, tmp_path)
    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = seed_eligible_asset(conn, media_root)
            if mutation == "image":
                conn.execute("UPDATE assets SET type = 'image' WHERE id = ?", (asset["id"],))
            elif mutation == "non_session":
                conn.execute("DELETE FROM upload_sessions WHERE asset_id = ?", (asset["id"],))
            elif mutation == "non_verified":
                conn.execute(
                    "UPDATE assets SET verification_status = 'server_hash_recorded' WHERE id = ?",
                    (asset["id"],),
                )
            elif mutation == "non_ready":
                conn.execute("UPDATE assets SET preview_status = 'failed' WHERE id = ?", (asset["id"],))
            elif mutation == "missing_active":
                conn.execute("UPDATE assets SET active_processed_result_id = NULL WHERE id = ?", (asset["id"],))
            elif mutation == "legacy_log":
                conn.execute("UPDATE assets SET is_log = 1 WHERE id = ?", (asset["id"],))
            elif mutation == "corrupt_result":
                (media_root / f"previews/{'a' * 32}.mp4").write_bytes(b"corrupt")
            before = conn.execute(
                "SELECT rendition_selection_generation, review_status, active_processed_result_id FROM assets WHERE id = ?",
                (asset["id"],),
            ).fetchone()
            conn.commit()

        response = client.post(
            f"/api/v1/assets/{asset['id']}/renditions", headers=auth(), json=request_body()
        )

    assert response.status_code == 409
    assert response.json() == {"code": "rendition_asset_not_eligible", "retryable": False}
    with connect(database_path, 5000) as conn:
        after = conn.execute(
            "SELECT rendition_selection_generation, review_status, active_processed_result_id FROM assets WHERE id = ?",
            (asset["id"],),
        ).fetchone()
        assert tuple(after) == tuple(before)
        assert conn.execute("SELECT COUNT(*) FROM renditions").fetchone()[0] == 0


def test_precondition_change_rolls_back_and_same_client_id_retries_once(monkeypatch, tmp_path):
    media_root, database_path = configure(monkeypatch, tmp_path)
    settings = Settings(
        media_root=media_root,
        api_token="secret-token",
        database_path=database_path,
    )
    with TestClient(app):
        with connect(database_path, 5000) as conn:
            asset = seed_eligible_asset(conn, media_root)
            conn.commit()

        def replace_active():
            content = b"replacement-preview"
            relative = f"previews/{'d' * 32}.mp4"
            (media_root / relative).write_bytes(content)
            with connect(database_path, 5000) as hook_conn:
                derived = insert_derived_file(
                    hook_conn,
                    asset_id=asset["id"],
                    kind="preview",
                    path=relative,
                    mime_type="video/mp4",
                    size_bytes=len(content),
                )
                result, _ = insert_ready_processed_result(
                    hook_conn,
                    asset_id=asset["id"],
                    derived_file_id=derived["id"],
                    mime_type="video/mp4",
                    size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    result_id="d" * 32,
                )
                set_active_processed_result(hook_conn, asset_id=asset["id"], result_id=result["id"])
                hook_conn.commit()

        with pytest.raises(RenditionCreationError) as error:
            create_rendition(
                settings=settings,
                asset_id=asset["id"],
                client_request_id="c" * 32,
                preset_id="compress-only",
                pre_transaction_hook=replace_active,
            )
        assert error.value.code == "rendition_precondition_changed"
        assert error.value.retryable is True

        retried = create_rendition(
            settings=settings,
            asset_id=asset["id"],
            client_request_id="c" * 32,
            preset_id="compress-only",
        )

    assert retried.replayed is False
    with connect(database_path, 5000) as conn:
        assert conn.execute("SELECT COUNT(*) FROM renditions").fetchone()[0] == 1
        assert conn.execute(
            "SELECT rendition_selection_generation FROM assets WHERE id = ?", (asset["id"],)
        ).fetchone()[0] == 1


@pytest.mark.parametrize("failure_step", ["after_generation", "after_job", "after_rendition"])
def test_each_creation_write_failure_rolls_back_all_state(monkeypatch, tmp_path, failure_step):
    media_root, database_path = configure(monkeypatch, tmp_path)
    settings = Settings(
        media_root=media_root,
        api_token="secret-token",
        database_path=database_path,
    )
    with TestClient(app):
        with connect(database_path, 5000) as conn:
            asset = seed_eligible_asset(conn, media_root)
            conn.commit()

        def fail(step):
            if step == failure_step:
                raise RuntimeError("injected")

        with pytest.raises(RuntimeError, match="injected"):
            create_rendition(
                settings=settings,
                asset_id=asset["id"],
                client_request_id="c" * 32,
                preset_id="compress-only",
                write_fault_injector=fail,
            )

    with connect(database_path, 5000) as conn:
        assert conn.execute("SELECT COUNT(*) FROM renditions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE job_type = 'rendition'").fetchone()[0] == 0
        assert conn.execute(
            "SELECT rendition_selection_generation FROM assets WHERE id = ?", (asset["id"],)
        ).fetchone()[0] == 0


def test_transaction_race_replay_returns_single_rendition(monkeypatch, tmp_path):
    media_root, database_path = configure(monkeypatch, tmp_path)
    settings = Settings(
        media_root=media_root,
        api_token="secret-token",
        database_path=database_path,
    )
    with TestClient(app):
        with connect(database_path, 5000) as conn:
            asset = seed_eligible_asset(conn, media_root)
            conn.commit()

        raced = []

        def create_competing_request():
            raced.append(
                create_rendition(
                    settings=settings,
                    asset_id=asset["id"],
                    client_request_id="c" * 32,
                    preset_id="compress-only",
                )
            )

        outer = create_rendition(
            settings=settings,
            asset_id=asset["id"],
            client_request_id="c" * 32,
            preset_id="compress-only",
            pre_transaction_hook=create_competing_request,
        )

    assert raced[0].replayed is False
    assert outer.replayed is True
    assert outer.representation == raced[0].representation


def test_rendition_api_validates_auth_body_and_same_asset_polling(monkeypatch, tmp_path):
    media_root, database_path = configure(monkeypatch, tmp_path)
    with TestClient(app) as client:
        with connect(database_path, 5000) as conn:
            asset = seed_eligible_asset(conn, media_root)
            other = seed_eligible_asset(conn, media_root, suffix="b")
            conn.commit()
        unauthorized = client.post(
            f"/api/v1/assets/{asset['id']}/renditions", json=request_body()
        )
        invalid = client.post(
            f"/api/v1/assets/{asset['id']}/renditions",
            headers=auth(),
            json={**request_body(), "unsafe_path": "/tmp/lut.cube"},
        )
        created = client.post(
            f"/api/v1/assets/{asset['id']}/renditions", headers=auth(), json=request_body()
        )
        cross_asset = client.get(
            f"/api/v1/assets/{other['id']}/renditions/{created.json()['rendition_id']}",
            headers=auth(),
        )

    assert unauthorized.status_code == 401
    assert invalid.status_code == 422
    assert invalid.json() == {"code": "validation_error", "retryable": False}
    assert "/tmp/lut.cube" not in invalid.text
    assert cross_asset.status_code == 404
    assert cross_asset.json() == {"code": "rendition_not_found", "retryable": False}
