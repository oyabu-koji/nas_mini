from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.assets import (
    count_assets,
    get_asset,
    insert_asset,
    list_assets,
    update_preview_status,
    update_review_status,
)
from app.repositories.derived_files import get_preview_for_asset, insert_derived_file
from app.repositories.jobs import (
    fail_job_and_asset_preview,
    insert_job,
    mark_job_done,
    mark_job_failed,
)


def _insert_asset(conn):
    ordinal = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    return insert_asset(
        conn,
        type="video",
        filename="clip.mov",
        original_path=f"originals/clip-{ordinal}.mov",
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


def test_derived_file_preview_helper_returns_newest_preview(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        asset = _insert_asset(conn)
        insert_derived_file(
            conn,
            asset_id=asset["id"],
            kind="preview",
            path="previews/old.mp4",
            mime_type="video/mp4",
            size_bytes=10,
        )
        inserted = insert_derived_file(
            conn,
            asset_id=asset["id"],
            kind="preview",
            path="previews/new.mp4",
            mime_type="video/mp4",
            size_bytes=20,
        )
        loaded = get_preview_for_asset(conn, asset["id"])

    assert loaded is not None
    assert loaded["id"] == inserted["id"]
    assert loaded["path"] == "previews/new.mp4"


def test_asset_list_count_and_review_helpers(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        first = _insert_asset(conn)
        second = _insert_asset(conn)
        listed = list_assets(conn, limit=10, offset=0)
        total = count_assets(conn)
        updated = update_review_status(conn, first["id"], "preview_confirmed")
        unchanged = get_asset(conn, first["id"])

    assert [asset["id"] for asset in listed] == [second["id"], first["id"]]
    assert total == 2
    assert updated is not None
    assert updated["review_status"] == "preview_confirmed"
    assert unchanged is not None
    assert unchanged["preview_status"] == first["preview_status"]
    assert unchanged["verification_status"] == first["verification_status"]
    assert unchanged["delete_candidate_status"] == first["delete_candidate_status"]


def test_update_review_status_returns_none_for_missing_asset(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        updated = update_review_status(conn, 999, "preview_confirmed")

    assert updated is None


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


def test_terminal_preview_failure_rolls_back_when_job_update_fails(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        with conn:
            asset = _insert_asset(conn)
            job = insert_job(
                conn,
                job_type="preview",
                asset_id=asset["id"],
                payload_json="{}",
            )
            conn.execute(
                """
                CREATE TRIGGER abort_preview_failure
                BEFORE UPDATE OF status ON jobs
                WHEN NEW.status = 'failed'
                BEGIN
                    SELECT RAISE(ABORT, 'forced job update failure');
                END;
                """
            )

        with pytest.raises(sqlite3.IntegrityError, match="forced job update failure"):
            fail_job_and_asset_preview(
                conn,
                job_id=job["id"],
                asset_id=asset["id"],
                error_message="preview failure",
            )

        asset_row = get_asset(conn, asset["id"])
        job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()

    assert asset_row is not None
    assert asset_row["preview_status"] == "preview_generating"
    assert job_row["status"] == "queued"
import sqlite3

import pytest
