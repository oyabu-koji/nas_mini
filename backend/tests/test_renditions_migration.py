import sqlite3

import pytest

from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.assets import insert_verified_video_asset
from app.repositories.derived_files import insert_derived_file
from app.repositories.jobs import insert_job
from app.repositories.processed_results import insert_ready_processed_result


def seed_asset_and_job(conn):
    asset = insert_verified_video_asset(
        conn,
        filename="clip.mov",
        original_path="originals/sessions/clip.mov",
        size_bytes=10,
        server_sha256="a" * 64,
        taken_at=None,
        latitude=None,
        longitude=None,
        exif_json=None,
        is_log=False,
    )
    job = insert_job(
        conn,
        job_type="rendition",
        asset_id=asset["id"],
        payload_json='{"rendition_id":"' + "b" * 32 + '"}',
    )
    return asset, job


def insert_queued_rendition(conn, asset_id, job_id, **overrides):
    values = {
        "id": "b" * 32,
        "asset_id": asset_id,
        "client_request_id": "c" * 32,
        "job_id": job_id,
        "selection_generation": 1,
        "requested_preset_id": "compress-only",
        "registry_classification": "valid",
        "state": "queued",
        "applied_preset_id": None,
        "color_transform_status": None,
        "error_code": None,
        "result_id": None,
    }
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    conn.execute(
        f"INSERT INTO renditions ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )


def test_007_migration_creates_tables_generation_and_ledger_once(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        run_migrations(conn)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(assets)")}
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        ledger_count = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = '007_managed_preview_presets'"
        ).fetchone()[0]

    assert "rendition_selection_generation" in columns
    assert {"renditions", "rendition_provenance"}.issubset(tables)
    assert ledger_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "UPPER"),
        ("client_request_id", "d" * 31),
        ("selection_generation", 0),
        ("requested_preset_id", "bad--id"),
        ("state", "unknown"),
    ],
)
def test_rendition_database_rejects_invalid_identity_and_state(tmp_path, field, value):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        asset, job = seed_asset_and_job(conn)
        with pytest.raises(sqlite3.IntegrityError):
            insert_queued_rendition(conn, asset["id"], job["id"], **{field: value})


def test_client_request_and_job_are_one_to_one(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        asset, job = seed_asset_and_job(conn)
        insert_queued_rendition(conn, asset["id"], job["id"])
        other_job = insert_job(conn, job_type="rendition", asset_id=asset["id"], payload_json="{}")

        with pytest.raises(sqlite3.IntegrityError):
            insert_queued_rendition(
                conn,
                asset["id"],
                other_job["id"],
                id="d" * 32,
                client_request_id="c" * 32,
                selection_generation=2,
            )


def test_rendition_job_must_be_same_asset_and_supported_type(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        asset, _job = seed_asset_and_job(conn)
        wrong_job = insert_job(conn, job_type="preview", asset_id=asset["id"], payload_json="{}")

        with pytest.raises(sqlite3.IntegrityError, match="rendition_job_relation_invalid"):
            insert_queued_rendition(conn, asset["id"], wrong_job["id"])


def test_rendition_base_identity_must_be_complete_and_same_asset(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        asset, job = seed_asset_and_job(conn)
        other_asset = insert_verified_video_asset(
            conn,
            filename="other.mov",
            original_path="originals/sessions/other.mov",
            size_bytes=10,
            server_sha256="e" * 64,
            taken_at=None,
            latitude=None,
            longitude=None,
            exif_json=None,
            is_log=False,
        )
        other_derived = insert_derived_file(
            conn,
            asset_id=other_asset["id"],
            kind="preview",
            path="previews/other.mp4",
            mime_type="video/mp4",
            size_bytes=10,
        )
        other_result, _created = insert_ready_processed_result(
            conn,
            asset_id=other_asset["id"],
            derived_file_id=other_derived["id"],
            mime_type="video/mp4",
            size_bytes=10,
            sha256="f" * 64,
        )

        with pytest.raises(sqlite3.IntegrityError):
            insert_queued_rendition(
                conn,
                asset["id"],
                job["id"],
                base_result_id=other_result["id"],
            )
        with pytest.raises(sqlite3.IntegrityError, match="rendition_base_relation_invalid"):
            insert_queued_rendition(
                conn,
                asset["id"],
                job["id"],
                base_result_id=other_result["id"],
                base_derived_file_id=other_derived["id"],
                base_result_sha256=other_result["sha256"],
            )
