import json

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.assets import insert_asset
from app.services.asset_read import build_asset_read_response, list_asset_reads


def _asset_row():
    return {
        "id": 1,
        "type": "video",
        "filename": "clip.mov",
        "original_path": "originals/clip.mov",
        "size_bytes": 10,
        "server_sha256": "abc123",
        "taken_at": "2026-06-25T10:00:00Z",
        "latitude": 35.0,
        "longitude": 139.0,
        "exif_json": json.dumps({"camera": "iPhone"}, separators=(",", ":")),
        "is_log": 0,
        "transfer_status": "uploaded",
        "verification_status": "server_hash_recorded",
        "preview_status": "preview_ready",
        "review_status": "not_reviewed",
        "delete_candidate_status": "not_candidate",
        "created_at": "2026-06-25 10:00:00",
        "updated_at": "2026-06-25 10:00:00",
    }


def test_build_asset_read_response_excludes_storage_paths():
    response = build_asset_read_response(
        asset=_asset_row(),
        preview={
            "id": 10,
            "kind": "preview",
            "path": "previews/clip.mp4",
            "mime_type": "video/mp4",
            "size_bytes": 123,
            "created_at": "2026-06-25 10:01:00",
        },
    )
    body = response.model_dump()

    assert body["exif_json"] == {"camera": "iPhone"}
    assert body["preview"]["url"] == "/assets/1/preview"
    assert "original_path" not in body
    assert "path" not in body["preview"]
    assert "local_delete_status" not in body


def test_build_asset_read_response_allows_missing_preview():
    response = build_asset_read_response(asset=_asset_row(), preview=None)

    assert response.preview is None


def test_asset_list_does_not_resolve_or_hash_processed_results(monkeypatch, tmp_path):
    settings = Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
    )
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        run_migrations(conn)
        insert_asset(
            conn,
            type="video",
            filename="clip.mov",
            original_path="originals/clip.mov",
            size_bytes=10,
            server_sha256="a" * 64,
            taken_at=None,
            latitude=None,
            longitude=None,
            exif_json=None,
            is_log=False,
        )

    def should_not_resolve(*_args, **_kwargs):
        raise AssertionError("asset list must not resolve processed results")

    monkeypatch.setattr("app.services.asset_read.resolve_deliverable_result", should_not_resolve)

    response = list_asset_reads(settings, limit=10, offset=0)

    assert len(response.items) == 1
    assert "active_processed_result" not in response.items[0].model_dump()
