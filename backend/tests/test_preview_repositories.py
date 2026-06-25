from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.assets import get_asset, insert_asset, update_preview_status
from app.repositories.derived_files import get_preview_for_asset, insert_derived_file
from app.repositories.jobs import insert_job, mark_job_done, mark_job_failed


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


def test_asset_preview_helpers(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        asset = _insert_asset(conn)
        update_preview_status(conn, asset["id"], "preview_ready")
        loaded = get_asset(conn, asset["id"])

    assert loaded is not None
    assert loaded["preview_status"] == "preview_ready"


def test_derived_file_preview_helpers(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        asset = _insert_asset(conn)
        inserted = insert_derived_file(
            conn,
            asset_id=asset["id"],
            kind="preview",
            path="previews/generated.mp4",
            mime_type="video/mp4",
            size_bytes=123,
        )
        loaded = get_preview_for_asset(conn, asset["id"])

    assert inserted["path"] == "previews/generated.mp4"
    assert loaded is not None
    assert loaded["mime_type"] == "video/mp4"


def test_job_done_and_failed_helpers(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        asset = _insert_asset(conn)
        job = insert_job(
            conn,
            job_type="preview",
            asset_id=asset["id"],
            payload_json="{}",
        )
        mark_job_failed(conn, job["id"], "x" * 250)
        failed = conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        mark_job_done(conn, job["id"])
        done = conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()

    assert failed["status"] == "failed"
    assert len(failed["error_message"]) == 200
    assert done["status"] == "done"
    assert done["error_message"] is None
