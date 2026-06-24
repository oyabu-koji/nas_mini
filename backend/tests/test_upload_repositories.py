import json

from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.assets import insert_asset
from app.repositories.jobs import insert_job


def test_insert_asset_stores_upload_statuses(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        asset = insert_asset(
            conn,
            type="image",
            filename="photo.jpg",
            original_path="originals/generated.jpg",
            size_bytes=10,
            server_sha256="abc123",
            taken_at=None,
            latitude=None,
            longitude=None,
            exif_json=None,
            is_log=False,
        )

    assert asset["transfer_status"] == "uploaded"
    assert asset["verification_status"] == "server_hash_recorded"
    assert asset["preview_status"] == "preview_generating"
    assert asset["review_status"] == "not_reviewed"
    assert asset["delete_candidate_status"] == "not_candidate"


def test_insert_job_stores_queued_preview_payload_with_relative_path(tmp_path):
    database_path = tmp_path / "db.sqlite3"
    payload = {
        "asset_id": 1,
        "original_path": "originals/generated.mov",
        "type": "video",
        "is_log": True,
    }

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        asset = insert_asset(
            conn,
            type="video",
            filename="clip.mov",
            original_path=payload["original_path"],
            size_bytes=10,
            server_sha256="abc123",
            taken_at=None,
            latitude=None,
            longitude=None,
            exif_json=None,
            is_log=True,
        )
        payload["asset_id"] = asset["id"]
        job = insert_job(
            conn,
            job_type="lut_preview",
            asset_id=asset["id"],
            payload_json=json.dumps(payload, separators=(",", ":")),
        )

    assert job["job_type"] == "lut_preview"
    assert job["status"] == "queued"
    assert json.loads(job["payload_json"]) == payload
    assert not payload["original_path"].startswith("/")
