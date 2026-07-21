import os
from datetime import datetime, timedelta, timezone

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.assets import insert_asset
from app.repositories.derived_files import insert_derived_file
from app.services.processed_result_recovery import recover_unreferenced_generated_previews
from app.services.storage import initialize_storage


def _settings(tmp_path) -> Settings:
    return Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
        job_lease_seconds=10,
        processed_result_recovery_grace_seconds=20,
    )


def test_recovery_removes_only_old_unreferenced_generated_previews(tmp_path):
    settings = _settings(tmp_path)
    initialize_storage(settings.media_root)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        run_migrations(conn)
        with conn:
            asset = insert_asset(
                conn,
                type="video",
                filename="clip.mov",
                original_path="originals/" + "a" * 32 + ".mov",
                size_bytes=10,
                server_sha256="a" * 64,
                taken_at=None,
                latitude=None,
                longitude=None,
                exif_json=None,
                is_log=False,
            )
            insert_derived_file(
                conn,
                asset_id=asset["id"],
                kind="preview",
                path="previews/" + "b" * 32 + ".mp4",
                mime_type="video/mp4",
                size_bytes=1,
            )

    old_unreferenced = settings.media_root / "previews" / ("c" * 32 + ".mp4")
    referenced = settings.media_root / "previews" / ("b" * 32 + ".mp4")
    recent_unreferenced = settings.media_root / "previews" / ("d" * 32 + ".jpg")
    non_generated_preview = settings.media_root / "previews" / "manual-preview.mp4"
    original = settings.media_root / "originals" / ("e" * 32 + ".mp4")
    photo_library_asset = tmp_path / "photo-library" / ("f" * 32 + ".mp4")
    for path in (
        old_unreferenced,
        referenced,
        recent_unreferenced,
        non_generated_preview,
        original,
        photo_library_asset,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    old_timestamp = (now - timedelta(seconds=31)).timestamp()
    for path in (old_unreferenced, referenced, non_generated_preview, original, photo_library_asset):
        os.utime(path, (old_timestamp, old_timestamp))

    removed = recover_unreferenced_generated_previews(settings=settings, now=now)

    assert removed == 1
    assert not old_unreferenced.exists()
    assert referenced.exists()
    assert recent_unreferenced.exists()
    assert non_generated_preview.exists()
    assert original.exists()
    assert photo_library_asset.exists()
