import json

from app.services.asset_read import build_asset_read_response


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
