import hashlib

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.assets import get_asset, insert_verified_video_asset, update_preview_status
from app.repositories.derived_files import insert_derived_file
from app.repositories.processed_results import (
    insert_ready_processed_result,
    set_active_processed_result,
)
from app.services.processed_result_delivery import resolve_deliverable_result
from app.services.storage import initialize_storage


def test_phase2b_formal_preview_gate_can_withhold_phase2a_result(tmp_path):
    settings = Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
    )
    initialize_storage(settings.media_root)
    content = b"processed-result"

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        run_migrations(conn)
        with conn:
            asset = insert_verified_video_asset(
                conn,
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
            update_preview_status(conn, asset["id"], "preview_ready")
            relative_path = "previews/" + "a" * 32 + ".mp4"
            (settings.media_root / relative_path).write_bytes(content)
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
                ) VALUES ('session', 'client', 'video', 'clip.mov', 10, ?, 10,
                          'originals/clip.mov', 'completed', ?, ?, ?)
                """,
                (
                    "a" * 64,
                    "2026-07-18T00:00:00+00:00",
                    "2026-07-25T00:00:00+00:00",
                    asset["id"],
                ),
            )
            asset = get_asset(conn, asset["id"])
            assert asset is not None

        observed = []

        def reject_formal_preview(observed_asset, observed_result, observed_derived):
            observed.append((observed_asset, observed_result, observed_derived))
            return False

        resolved = resolve_deliverable_result(
            settings=settings,
            conn=conn,
            asset=asset,
            formal_preview_provenance_validator=reject_formal_preview,
        )

    assert resolved is None
    assert observed[0][1]["id"] == result["id"]
    assert observed[0][2]["id"] == derived["id"]
