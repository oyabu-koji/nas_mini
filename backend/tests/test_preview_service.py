import json
from pathlib import Path

import pytest

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.assets import insert_asset
from app.repositories.derived_files import insert_derived_file
from app.repositories.jobs import insert_job
from app.services import ffmpeg
from app.services.preview import (
    _cleanup_confirmed_preview,
    _cleanup_tmp_preview,
    process_preview_job,
)
from app.services.storage import initialize_storage


def _settings(tmp_path, lut_path=None) -> Settings:
    return Settings(
        media_root=tmp_path / "media",
        api_token="secret-token",
        database_path=tmp_path / "db.sqlite3",
        lut_path=lut_path or tmp_path / "rec709.cube",
    )


def _prepare(settings: Settings) -> None:
    initialize_storage(settings.media_root)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        run_migrations(conn)


def _asset_and_job(
    settings: Settings,
    *,
    asset_type: str = "video",
    job_type: str = "preview",
    original_path: str = "originals/input.mov",
    payload_asset_id: int | None = None,
):
    original = settings.media_root / original_path
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"original")

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        asset = insert_asset(
            conn,
            type=asset_type,
            filename="input.mov",
            original_path=original_path,
            size_bytes=8,
            server_sha256="abc123",
            taken_at=None,
            latitude=None,
            longitude=None,
            exif_json=None,
            is_log=job_type == "lut_preview",
        )
        payload = {
            "asset_id": payload_asset_id if payload_asset_id is not None else asset["id"],
            "original_path": original_path,
            "type": asset_type,
            "is_log": job_type == "lut_preview",
        }
        job = insert_job(
            conn,
            job_type=job_type,
            asset_id=asset["id"],
            payload_json=json.dumps(payload, separators=(",", ":")),
        )
    return asset, job


def _run_ffmpeg_writes_output(command, timeout_seconds):
    Path(command[-1]).write_bytes(b"preview")


def _run_ffmpeg_fails_after_tmp_write(command, timeout_seconds):
    Path(command[-1]).write_bytes(b"partial")
    raise ffmpeg.PreviewGenerationError("ffmpeg failed")


def _job_row(settings: Settings, job_id: int):
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def _asset_row(settings: Settings, asset_id: int):
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        return conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()


def test_process_preview_job_success_video(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, job = _asset_and_job(settings)

    processed = process_preview_job(
        settings=settings,
        job=job,
        run_ffmpeg=_run_ffmpeg_writes_output,
    )

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        derived = conn.execute("SELECT * FROM derived_files").fetchone()

    assert processed is True
    assert derived["kind"] == "preview"
    assert derived["path"].startswith("previews/")
    assert derived["mime_type"] == "video/mp4"
    assert (settings.media_root / derived["path"]).read_bytes() == b"preview"
    assert (settings.media_root / asset["original_path"]).read_bytes() == b"original"
    assert _asset_row(settings, asset["id"])["preview_status"] == "preview_ready"
    assert _job_row(settings, job["id"])["status"] == "done"


def test_process_preview_job_success_image(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, job = _asset_and_job(
        settings,
        asset_type="image",
        job_type="preview",
        original_path="originals/input.jpg",
    )

    process_preview_job(settings=settings, job=job, run_ffmpeg=_run_ffmpeg_writes_output)

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        derived = conn.execute("SELECT * FROM derived_files").fetchone()

    assert derived["mime_type"] == "image/jpeg"
    assert derived["path"].endswith(".jpg")
    assert _asset_row(settings, asset["id"])["preview_status"] == "preview_ready"


def test_process_preview_job_success_lut_preview_uses_lut(tmp_path):
    lut_path = tmp_path / "rec709.cube"
    lut_path.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")
    settings = _settings(tmp_path, lut_path=lut_path)
    _prepare(settings)
    _asset, job = _asset_and_job(settings, job_type="lut_preview")
    commands = []

    def record_command(command, timeout_seconds):
        commands.append(command)
        Path(command[-1]).write_bytes(b"preview")

    process_preview_job(settings=settings, job=job, run_ffmpeg=record_command)

    assert str(lut_path) in commands[0][commands[0].index("-vf") + 1]


def test_process_preview_job_uses_jobs_asset_id_as_source_of_truth(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, job = _asset_and_job(settings, payload_asset_id=9999)

    process_preview_job(settings=settings, job=job, run_ffmpeg=_run_ffmpeg_writes_output)

    assert _asset_row(settings, asset["id"])["preview_status"] == "failed"
    assert _job_row(settings, job["id"])["status"] == "failed"
    assert "mismatch" in _job_row(settings, job["id"])["error_message"]


def test_process_preview_job_missing_asset_fails_job(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        job = insert_job(conn, job_type="preview", asset_id=None, payload_json="{}")

    process_preview_job(settings=settings, job=job, run_ffmpeg=_run_ffmpeg_writes_output)

    row = _job_row(settings, job["id"])
    assert row["status"] == "failed"
    assert row["error_message"] == "asset missing"


def test_process_preview_job_missing_original_fails_job_and_asset(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, job = _asset_and_job(settings)
    (settings.media_root / asset["original_path"]).unlink()

    process_preview_job(settings=settings, job=job, run_ffmpeg=_run_ffmpeg_writes_output)

    assert _asset_row(settings, asset["id"])["preview_status"] == "failed"
    assert _job_row(settings, job["id"])["error_message"] == "original file missing"


def test_process_preview_job_unsafe_original_path_fails_without_host_path(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        asset = insert_asset(
            conn,
            type="image",
            filename="photo.jpg",
            original_path="../outside.jpg",
            size_bytes=8,
            server_sha256="abc123",
            taken_at=None,
            latitude=None,
            longitude=None,
            exif_json=None,
            is_log=False,
        )
        job = insert_job(conn, job_type="preview", asset_id=asset["id"], payload_json="{}")

    process_preview_job(settings=settings, job=job, run_ffmpeg=_run_ffmpeg_writes_output)

    row = _job_row(settings, job["id"])
    assert row["status"] == "failed"
    assert row["error_message"] == "unsafe original path"
    assert str(settings.media_root) not in row["error_message"]


def test_process_preview_job_missing_lut_fails_lut_preview(tmp_path):
    settings = _settings(tmp_path, lut_path=tmp_path / "missing.cube")
    _prepare(settings)
    asset, job = _asset_and_job(settings, job_type="lut_preview")

    process_preview_job(settings=settings, job=job, run_ffmpeg=_run_ffmpeg_writes_output)

    assert _asset_row(settings, asset["id"])["preview_status"] == "failed"
    assert _job_row(settings, job["id"])["error_message"] == "lut file missing"


def test_process_preview_job_image_lut_preview_fails(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, job = _asset_and_job(
        settings,
        asset_type="image",
        job_type="lut_preview",
        original_path="originals/input.jpg",
    )

    process_preview_job(settings=settings, job=job, run_ffmpeg=_run_ffmpeg_writes_output)

    assert _asset_row(settings, asset["id"])["preview_status"] == "failed"
    assert _job_row(settings, job["id"])["error_message"] == "lut preview requires video asset"


def test_process_preview_job_ffmpeg_failure_cleans_tmp_and_fails(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, job = _asset_and_job(settings)

    process_preview_job(
        settings=settings,
        job=job,
        run_ffmpeg=_run_ffmpeg_fails_after_tmp_write,
    )

    assert list((settings.media_root / "tmp").iterdir()) == []
    assert _asset_row(settings, asset["id"])["preview_status"] == "failed"
    assert _job_row(settings, job["id"])["error_message"] == "ffmpeg failed"


def test_process_preview_job_existing_preview_file_marks_done_without_ffmpeg(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, job = _asset_and_job(settings)
    preview_relative_path = "previews/existing.mp4"
    preview_path = settings.media_root / preview_relative_path
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_bytes(b"existing")
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        insert_derived_file(
            conn,
            asset_id=asset["id"],
            kind="preview",
            path=preview_relative_path,
            mime_type="video/mp4",
            size_bytes=8,
        )

    def should_not_run(command, timeout_seconds):
        raise AssertionError("ffmpeg should not run")

    process_preview_job(settings=settings, job=job, run_ffmpeg=should_not_run)

    assert _job_row(settings, job["id"])["status"] == "done"
    assert _asset_row(settings, asset["id"])["preview_status"] == "preview_ready"


def test_process_preview_job_existing_preview_record_missing_file_fails(tmp_path):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, job = _asset_and_job(settings)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        insert_derived_file(
            conn,
            asset_id=asset["id"],
            kind="preview",
            path="previews/missing.mp4",
            mime_type="video/mp4",
            size_bytes=8,
        )

    process_preview_job(settings=settings, job=job, run_ffmpeg=_run_ffmpeg_writes_output)

    assert _job_row(settings, job["id"])["status"] == "failed"
    assert _asset_row(settings, asset["id"])["preview_status"] == "failed"


def test_process_preview_job_db_failure_after_move_cleans_confirmed_preview(
    monkeypatch,
    tmp_path,
):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, job = _asset_and_job(settings)

    def raise_insert_error(*args, **kwargs):
        raise RuntimeError("database write failed")

    monkeypatch.setattr("app.services.preview.insert_derived_file", raise_insert_error)

    processed = process_preview_job(
        settings=settings,
        job=job,
        run_ffmpeg=_run_ffmpeg_writes_output,
    )

    assert processed is True
    assert list((settings.media_root / "previews").iterdir()) == []
    assert _asset_row(settings, asset["id"])["preview_status"] == "failed"
    job_row = _job_row(settings, job["id"])
    assert job_row["status"] == "failed"
    assert job_row["error_message"] == "database failure"


def test_process_preview_job_mkdir_failure_marks_storage_failure(
    monkeypatch,
    tmp_path,
):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, job = _asset_and_job(settings)
    host_path = settings.media_root / "previews"

    def raise_mkdir_error(self, *args, **kwargs):
        if self == host_path:
            raise OSError(13, "Permission denied", str(host_path))
        return original_mkdir(self, *args, **kwargs)

    original_mkdir = Path.mkdir
    monkeypatch.setattr(Path, "mkdir", raise_mkdir_error)

    process_preview_job(settings=settings, job=job, run_ffmpeg=_run_ffmpeg_writes_output)

    assert _asset_row(settings, asset["id"])["preview_status"] == "failed"
    job_row = _job_row(settings, job["id"])
    assert job_row["status"] == "failed"
    assert job_row["error_message"] == "storage failure"
    assert str(host_path) not in job_row["error_message"]


def test_process_preview_job_replace_failure_marks_storage_failure(
    monkeypatch,
    tmp_path,
):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, job = _asset_and_job(settings)
    host_path = settings.media_root / "previews" / "blocked.mp4"

    def raise_replace_error(self, target):
        raise OSError(28, "No space left on device", str(host_path))

    monkeypatch.setattr(Path, "replace", raise_replace_error)

    process_preview_job(settings=settings, job=job, run_ffmpeg=_run_ffmpeg_writes_output)

    assert list((settings.media_root / "tmp").iterdir()) == []
    assert _asset_row(settings, asset["id"])["preview_status"] == "failed"
    job_row = _job_row(settings, job["id"])
    assert job_row["status"] == "failed"
    assert job_row["error_message"] == "storage failure"
    assert str(host_path) not in job_row["error_message"]


def test_process_preview_job_stat_failure_marks_storage_failure_and_cleans_preview(
    monkeypatch,
    tmp_path,
):
    settings = _settings(tmp_path)
    _prepare(settings)
    asset, job = _asset_and_job(settings)
    host_path = settings.media_root / "previews" / "preview.mp4"

    def raise_stat_error(self, *args, **kwargs):
        if "previews" in self.parts:
            raise OSError(5, "Input/output error", str(host_path))
        return original_stat(self, *args, **kwargs)

    original_stat = Path.stat
    monkeypatch.setattr(Path, "stat", raise_stat_error)

    process_preview_job(settings=settings, job=job, run_ffmpeg=_run_ffmpeg_writes_output)

    assert list((settings.media_root / "previews").iterdir()) == []
    assert _asset_row(settings, asset["id"])["preview_status"] == "failed"
    job_row = _job_row(settings, job["id"])
    assert job_row["status"] == "failed"
    assert job_row["error_message"] == "storage failure"
    assert str(host_path) not in job_row["error_message"]


def test_confirmed_preview_cleanup_log_excludes_host_path(caplog, monkeypatch, tmp_path):
    host_path = tmp_path / "media" / "previews" / "saved.mp4"
    relative_path = "previews/saved.mp4"

    def raise_cleanup_error(self):
        raise OSError(13, "Permission denied", str(host_path))

    monkeypatch.setattr(Path, "unlink", raise_cleanup_error)

    _cleanup_confirmed_preview(host_path, relative_path)

    assert str(host_path) not in caplog.text
    assert relative_path in caplog.text


def test_tmp_preview_cleanup_log_excludes_host_path(caplog, monkeypatch, tmp_path):
    host_path = tmp_path / "media" / "tmp" / "preview.mp4"

    def raise_cleanup_error(self):
        raise OSError(13, "Permission denied", str(host_path))

    monkeypatch.setattr(Path, "unlink", raise_cleanup_error)

    _cleanup_tmp_preview(host_path)

    assert str(host_path) not in caplog.text
    assert "Tmp preview cleanup failed" in caplog.text
