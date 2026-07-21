import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.assets import insert_verified_video_asset
from app.repositories.derived_files import insert_derived_file
from app.repositories.jobs import insert_job
from app.services.processed_result_backfill import (
    BACKFILL_INTEGRITY_FAILURE_CODE,
    BACKFILL_RETRYABLE_FAILURE_CODE,
    backfill_eligible_processed_results,
    backfill_processed_result_for_asset,
)
from app.services.storage import initialize_storage


def _settings(tmp_path) -> Settings:
    return Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
    )


def _prepare(settings: Settings) -> None:
    initialize_storage(settings.media_root)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        run_migrations(conn)


def _create_video_candidate(
    settings: Settings,
    *,
    name: str = "clip",
    content: bytes = b"processed-video",
    completed_session: bool = True,
    is_log: bool = False,
):
    relative_path = f"previews/{name}.mp4"
    preview_path = settings.media_root / relative_path
    preview_path.write_bytes(content)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        with conn:
            asset = insert_verified_video_asset(
                conn,
                filename=f"{name}.mov",
                original_path=f"originals/{name}.mov",
                size_bytes=10,
                server_sha256=hashlib.sha256(name.encode()).hexdigest(),
                taken_at=None,
                latitude=None,
                longitude=None,
                exif_json=None,
                is_log=is_log,
            )
            conn.execute(
                "UPDATE assets SET preview_status = 'preview_ready' WHERE id = ?",
                (asset["id"],),
            )
            derived = insert_derived_file(
                conn,
                asset_id=asset["id"],
                kind="preview",
                path=relative_path,
                mime_type="video/mp4",
                size_bytes=len(content),
            )
            if completed_session:
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
    return asset, derived, preview_path


def test_backfill_creates_one_active_result_and_is_idempotent(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, derived, _preview_path = _create_video_candidate(settings)

    first = backfill_processed_result_for_asset(settings=settings, asset_id=asset["id"])
    second = backfill_processed_result_for_asset(settings=settings, asset_id=asset["id"])

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        active = conn.execute(
            """
            SELECT processed_results.*
            FROM assets
            JOIN processed_results ON processed_results.id = assets.active_processed_result_id
            WHERE assets.id = ?
            """,
            (asset["id"],),
        ).fetchone()

    assert first is not None
    assert first.status == "created"
    assert second is not None
    assert second.status == "already_active"
    assert active is not None
    assert active["derived_file_id"] == derived["id"]
    assert active["sha256"] == hashlib.sha256(b"processed-video").hexdigest()


def test_startup_backfill_persists_terminal_job_and_asset_outcomes(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    success_asset, _success_derived, _success_path = _create_video_candidate(settings, name="startup-success")
    failed_asset, _failed_derived, failed_path = _create_video_candidate(settings, name="startup-failed")
    failed_path.unlink()
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        with conn:
            success_job = insert_job(
                conn,
                job_type="preview",
                asset_id=success_asset["id"],
                payload_json="{}",
                dedup_key="preview:startup-success",
            )
            failed_job = insert_job(
                conn,
                job_type="preview",
                asset_id=failed_asset["id"],
                payload_json="{}",
                dedup_key="preview:startup-failed",
            )
            conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (success_job["id"],))
            conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (failed_job["id"],))

    outcomes = backfill_eligible_processed_results(settings=settings)

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        success_job_after = conn.execute("SELECT * FROM jobs WHERE id = ?", (success_job["id"],)).fetchone()
        failed_job_after = conn.execute("SELECT * FROM jobs WHERE id = ?", (failed_job["id"],)).fetchone()
        failed_asset_after = conn.execute("SELECT * FROM assets WHERE id = ?", (failed_asset["id"],)).fetchone()

    assert {outcome.status for outcome in outcomes} == {"created", "integrity_failed"}
    assert success_job_after["status"] == "done"
    assert failed_job_after["status"] == "failed"
    assert failed_job_after["error_message"] == BACKFILL_INTEGRITY_FAILURE_CODE
    assert failed_asset_after["preview_status"] == "failed"


def test_backfill_ignores_phase1_direct_video_log_and_failed_preview(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    direct_asset, _direct_derived, _ = _create_video_candidate(
        settings,
        name="direct",
        completed_session=False,
    )
    log_asset, _log_derived, _ = _create_video_candidate(
        settings,
        name="log",
        completed_session=True,
        is_log=True,
    )
    failed_asset, _failed_derived, _ = _create_video_candidate(
        settings,
        name="failed",
        completed_session=True,
    )
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute(
            "UPDATE assets SET preview_status = 'failed' WHERE id = ?",
            (failed_asset["id"],),
        )

    outcomes = backfill_eligible_processed_results(settings=settings)
    direct_outcome = backfill_processed_result_for_asset(settings=settings, asset_id=direct_asset["id"])
    log_outcome = backfill_processed_result_for_asset(settings=settings, asset_id=log_asset["id"])

    assert outcomes == []
    assert direct_outcome is None
    assert log_outcome is None


def test_backfill_does_not_advertise_missing_or_corrupt_preview(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    missing_asset, _missing_derived, missing_path = _create_video_candidate(
        settings,
        name="missing",
    )
    missing_path.unlink()
    corrupt_asset, corrupt_derived, corrupt_path = _create_video_candidate(
        settings,
        name="corrupt",
    )
    corrupt_path.write_bytes(b"wrong-size")

    missing = backfill_processed_result_for_asset(settings=settings, asset_id=missing_asset["id"])
    corrupt = backfill_processed_result_for_asset(settings=settings, asset_id=corrupt_asset["id"])

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        active_count = conn.execute(
            "SELECT COUNT(*) FROM assets WHERE active_processed_result_id IS NOT NULL"
        ).fetchone()[0]
        result_count = conn.execute("SELECT COUNT(*) FROM processed_results").fetchone()[0]

    assert missing is not None
    assert missing.status == "integrity_failed"
    assert missing.error_code == BACKFILL_INTEGRITY_FAILURE_CODE
    assert corrupt is not None
    assert corrupt.status == "integrity_failed"
    assert corrupt.error_code == BACKFILL_INTEGRITY_FAILURE_CODE
    assert corrupt_derived["id"] > 0
    assert active_count == 0
    assert result_count == 0


def test_concurrent_backfill_creates_at_most_one_active_result(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, _derived, _preview_path = _create_video_candidate(settings, name="concurrent")

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda _unused: backfill_processed_result_for_asset(
                    settings=settings,
                    asset_id=asset["id"],
                ),
                range(2),
            )
        )

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        active_count = conn.execute(
            "SELECT COUNT(*) FROM assets WHERE id = ? AND active_processed_result_id IS NOT NULL",
            (asset["id"],),
        ).fetchone()[0]
        result_count = conn.execute(
            "SELECT COUNT(*) FROM processed_results WHERE asset_id = ?",
            (asset["id"],),
        ).fetchone()[0]

    assert {outcome.status for outcome in outcomes if outcome is not None} == {
        "created",
        "already_active",
    }
    assert active_count == 1
    assert result_count == 1


def test_backfill_reports_database_failure_as_retryable(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, _derived, _preview_path = _create_video_candidate(settings, name="retryable")

    def raise_operational_error(*_args, **_kwargs):
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(
        "app.services.processed_result_backfill.insert_ready_processed_result",
        raise_operational_error,
    )

    outcome = backfill_processed_result_for_asset(settings=settings, asset_id=asset["id"])

    assert outcome is not None
    assert outcome.status == "retryable_failure"
    assert outcome.error_code == BACKFILL_RETRYABLE_FAILURE_CODE
