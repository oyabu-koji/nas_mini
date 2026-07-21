import hashlib

import pytest

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.assets import insert_verified_video_asset
from app.repositories.derived_files import insert_derived_file
from app.repositories.jobs import insert_job
from app.services.processed_result_finalizer import finalize_ready_processed_result
from app.services.storage import initialize_storage


def _settings(tmp_path) -> Settings:
    return Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
    )


def _prepare_phase2a_video(settings: Settings, *, name: str = "clip"):
    initialize_storage(settings.media_root)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        run_migrations(conn)
        with conn:
            asset = insert_verified_video_asset(
                conn,
                filename=f"{name}.mov",
                original_path=f"originals/{name}.mov",
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
                job_type="preview",
                asset_id=asset["id"],
                payload_json="{}",
                dedup_key=f"preview:{name}",
            )
            conn.execute(
                """
                INSERT INTO upload_sessions (
                    id, client_upload_id, type, filename, size_bytes,
                    expected_file_sha256, chunk_size_bytes, original_relative_path,
                    status, last_activity_at, expires_at, asset_id
                ) VALUES (?, ?, 'video', ?, ?, ?, ?, ?, 'completed', ?, ?, ?)
                """,
                (
                    f"session-{name}",
                    f"client-{name}",
                    f"{name}.mov",
                    10,
                    "a" * 64,
                    10,
                    f"originals/{name}.mov",
                    "2026-07-18T00:00:00+00:00",
                    "2026-07-25T00:00:00+00:00",
                    asset["id"],
                ),
            )
    return asset, job


def test_finalizer_commits_phase2a_preview_and_active_result_together(tmp_path):
    settings = _settings(tmp_path)
    asset, job = _prepare_phase2a_video(settings)
    content = b"processed-preview"
    relative_path = "previews/" + "a" * 32 + ".mp4"
    (settings.media_root / relative_path).write_bytes(content)

    finalize_ready_processed_result(
        settings=settings,
        job_id=job["id"],
        asset_id=asset["id"],
        preview_relative_path=relative_path,
        mime_type="video/mp4",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        asset_after = conn.execute("SELECT * FROM assets WHERE id = ?", (asset["id"],)).fetchone()
        result = conn.execute("SELECT * FROM processed_results").fetchone()
        job_after = conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        derived = conn.execute("SELECT * FROM derived_files").fetchone()

    assert derived is not None
    assert result is not None
    assert result["derived_file_id"] == derived["id"]
    assert asset_after["active_processed_result_id"] == result["id"]
    assert asset_after["preview_status"] == "preview_ready"
    assert job_after["status"] == "done"


@pytest.mark.parametrize(
    "failure_step",
    [
        "after_derived_file",
        "after_ready_result",
        "after_active_pointer",
        "after_preview_status",
        "after_job_done",
    ],
)
def test_finalizer_rolls_back_every_write_boundary_on_failure(tmp_path, failure_step):
    settings = _settings(tmp_path)
    asset, job = _prepare_phase2a_video(settings, name=f"fault-{failure_step}")
    content = b"processed-preview"
    relative_path = "previews/" + "b" * 32 + ".mp4"
    (settings.media_root / relative_path).write_bytes(content)

    def fail_at(step: str) -> None:
        if step == failure_step:
            raise RuntimeError("forced finalizer failure")

    with pytest.raises(RuntimeError, match="forced finalizer failure"):
        finalize_ready_processed_result(
            settings=settings,
            job_id=job["id"],
            asset_id=asset["id"],
            preview_relative_path=relative_path,
            mime_type="video/mp4",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            fault_injector=fail_at,
        )

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        asset_after = conn.execute("SELECT * FROM assets WHERE id = ?", (asset["id"],)).fetchone()
        result_count = conn.execute("SELECT COUNT(*) FROM processed_results").fetchone()[0]
        derived_count = conn.execute("SELECT COUNT(*) FROM derived_files").fetchone()[0]
        job_after = conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()

    assert asset_after["active_processed_result_id"] is None
    assert asset_after["preview_status"] == "preview_generating"
    assert result_count == 0
    assert derived_count == 0
    assert job_after["status"] == "queued"


def test_finalizer_existing_preview_creates_missing_active_result(tmp_path):
    settings = _settings(tmp_path)
    asset, job = _prepare_phase2a_video(settings, name="existing")
    content = b"existing-preview"
    relative_path = "previews/" + "c" * 32 + ".mp4"
    (settings.media_root / relative_path).write_bytes(content)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        with conn:
            derived = insert_derived_file(
                conn,
                asset_id=asset["id"],
                kind="preview",
                path=relative_path,
                mime_type="video/mp4",
                size_bytes=len(content),
            )

    finalize_ready_processed_result(
        settings=settings,
        job_id=job["id"],
        asset_id=asset["id"],
        preview_relative_path=relative_path,
        mime_type="video/mp4",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        existing_derived_file_id=derived["id"],
    )

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        asset_after = conn.execute("SELECT * FROM assets WHERE id = ?", (asset["id"],)).fetchone()
        result = conn.execute("SELECT * FROM processed_results").fetchone()

    assert result is not None
    assert asset_after["active_processed_result_id"] == result["id"]


def test_finalizer_supersedes_old_active_result_before_activating_replacement(tmp_path):
    settings = _settings(tmp_path)
    asset, first_job = _prepare_phase2a_video(settings, name="replacement")
    first_content = b"first-preview"
    first_path = "previews/" + "d" * 32 + ".mp4"
    (settings.media_root / first_path).write_bytes(first_content)
    finalize_ready_processed_result(
        settings=settings,
        job_id=first_job["id"],
        asset_id=asset["id"],
        preview_relative_path=first_path,
        mime_type="video/mp4",
        size_bytes=len(first_content),
        sha256=hashlib.sha256(first_content).hexdigest(),
    )

    second_content = b"second-preview"
    second_path = "previews/" + "e" * 32 + ".mp4"
    (settings.media_root / second_path).write_bytes(second_content)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        with conn:
            second_job = insert_job(
                conn,
                job_type="preview",
                asset_id=asset["id"],
                payload_json="{}",
                dedup_key="preview:replacement-two",
            )

    finalize_ready_processed_result(
        settings=settings,
        job_id=second_job["id"],
        asset_id=asset["id"],
        preview_relative_path=second_path,
        mime_type="video/mp4",
        size_bytes=len(second_content),
        sha256=hashlib.sha256(second_content).hexdigest(),
    )

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        results = conn.execute(
            "SELECT id, status FROM processed_results WHERE asset_id = ? ORDER BY created_at, id",
            (asset["id"],),
        ).fetchall()
        active = conn.execute(
            "SELECT active_processed_result_id FROM assets WHERE id = ?",
            (asset["id"],),
        ).fetchone()

    ready_results = [result for result in results if result["status"] == "ready"]
    superseded_results = [result for result in results if result["status"] == "superseded"]
    assert len(ready_results) == 1
    assert len(superseded_results) == 1
    assert active["active_processed_result_id"] == ready_results[0]["id"]
