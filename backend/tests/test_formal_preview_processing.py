import hashlib
import json
from dataclasses import replace

import pytest

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.formal_previews import (
    save_detection_snapshot,
    save_preset_snapshot,
    transition_formal_preview_attempt,
)
from app.repositories.jobs import claim_next_job, insert_or_return_job
from app.services.apple_log_detector import DetectionResult
from app.services.canonical_json import canonical_json_bytes
from app.services.formal_preview_processing import (
    FormalPreviewProcessingError,
    parse_formal_preview_payload,
    prepare_formal_preview_attempt,
    process_formal_preview_job,
    _resolve_preset,
)
from app.services.phase2b_migration import apply_phase2b_migration
from tests.detector_test_support import write_detector_artifacts
from tests.test_phase2b_schema import _insert_session_asset
from app.workers.worker import run_once


def _settings(tmp_path):
    settings = Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
        detector_root=tmp_path / "detector",
    )
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)
    apply_phase2b_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        certification_check=lambda _settings: None,
    )
    return settings


def _claimed_formal_job(settings, *, payload, asset_generation=1):
    with connect(settings.database_path, 5000) as conn:
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        conn.execute(
            "UPDATE assets SET preview_generation = ? WHERE id = 1",
            (asset_generation,),
        )
        job, _ = insert_or_return_job(
            conn,
            job_type="preview",
            asset_id=1,
            payload_json=json.dumps(payload, separators=(",", ":")),
            dedup_key="formal-preview",
            preview_generation=1,
        )
        conn.commit()
        claimed = claim_next_job(conn, 300, {"preview"})
    assert claimed is not None and claimed["id"] == job["id"]
    return claimed


def _prepare_verified_original(settings, *, content=b"video-source"):
    digest = hashlib.sha256(content).hexdigest()
    relative_path = "originals/sessions/session-one.mov"
    original_path = settings.media_root / relative_path
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(content)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            """
            UPDATE assets
            SET original_path = ?, size_bytes = ?, server_sha256 = ?
            WHERE id = 1
            """,
            (relative_path, len(content), digest),
        )
        conn.execute(
            """
            UPDATE upload_sessions
            SET original_relative_path = ?, size_bytes = ?,
                expected_file_sha256 = ?
            WHERE asset_id = 1
            """,
            (relative_path, len(content), digest),
        )
        conn.commit()
    return original_path


def _detection(status):
    evidence = canonical_json_bytes({"classification": status, "values": []})
    return DetectionResult(
        status=status,
        source_profile=None,
        evidence_sha256=hashlib.sha256(evidence).hexdigest(),
        evidence_json=evidence,
    )


def _run_formal_success(settings, job, *, status, monkeypatch):
    write_detector_artifacts(settings.detector_root)
    monkeypatch.setattr(
        "app.services.formal_preview_processing.read_ffprobe_version",
        lambda **_kwargs: "ffprobe test pinned",
    )
    commands = []

    def render(command):
        commands.append(command)
        output = __import__("pathlib").Path(command[-1])
        output.write_bytes(b"encoded-preview")

    assert process_formal_preview_job(
        settings=settings,
        job=job,
        probe_runner=lambda _path, _manifest: _detection(status),
        render_runner=render,
    )
    return commands


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"asset_id": True, "preview_generation": 1, "detection_required": True},
        {"asset_id": 1, "preview_generation": 1.0, "detection_required": True},
        {"asset_id": 1, "preview_generation": 1, "detection_required": 1},
    ],
)
def test_formal_payload_requires_object_integer_generation_and_boolean(payload):
    with pytest.raises(FormalPreviewProcessingError):
        parse_formal_preview_payload({"payload_json": json.dumps(payload)})


def test_invalid_payload_finishes_attempt_job_and_asset_as_failed(tmp_path):
    settings = _settings(tmp_path)
    job = _claimed_formal_job(
        settings,
        payload={"asset_id": 1, "preview_generation": 1},
    )

    assert prepare_formal_preview_attempt(settings=settings, job=job) is None

    with connect(settings.database_path, 5000) as conn:
        attempt = conn.execute("SELECT * FROM formal_preview_attempts").fetchone()
        current_job = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job["id"],)
        ).fetchone()
        asset = conn.execute("SELECT * FROM assets WHERE id = 1").fetchone()
    assert attempt["state"] == "failed"
    assert current_job["status"] == "failed"
    assert current_job["claimed_at"] is None
    assert current_job["lease_expires_at"] is None
    assert asset["preview_status"] == "failed"


def test_stale_generation_is_superseded_without_mutating_asset_state(tmp_path):
    settings = _settings(tmp_path)
    job = _claimed_formal_job(
        settings,
        payload={
            "asset_id": 1,
            "preview_generation": 1,
            "detection_required": True,
        },
    )
    with connect(settings.database_path, 5000) as conn:
        conn.execute("UPDATE assets SET preview_generation = 2 WHERE id = 1")
        conn.commit()

    assert prepare_formal_preview_attempt(settings=settings, job=job) is None

    with connect(settings.database_path, 5000) as conn:
        attempt = conn.execute("SELECT * FROM formal_preview_attempts").fetchone()
        current_job = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job["id"],)
        ).fetchone()
        asset = conn.execute("SELECT * FROM assets WHERE id = 1").fetchone()
    assert attempt["state"] == "superseded"
    assert current_job["error_message"] == "preview_generation_superseded"
    assert current_job["claimed_at"] is None
    assert asset["preview_generation"] == 2
    assert asset["preview_status"] == "preview_generating"


def test_terminal_attempt_converges_recovered_job_without_reprocessing(tmp_path):
    settings = _settings(tmp_path)
    job = _claimed_formal_job(
        settings,
        payload={
            "asset_id": 1,
            "preview_generation": 1,
            "detection_required": True,
        },
    )
    attempt = prepare_formal_preview_attempt(settings=settings, job=job)
    assert attempt is not None
    with connect(settings.database_path, 5000) as conn:
        failed = transition_formal_preview_attempt(
            conn,
            attempt_id=attempt["id"],
            new_state="failed",
            failure_code="formal_preview_render_failed",
        )
        conn.execute(
            """
            UPDATE jobs
            SET status = 'running', claimed_at = CURRENT_TIMESTAMP,
                lease_expires_at = datetime('now', '+1 minute')
            WHERE id = ?
            """,
            (job["id"],),
        )
        conn.commit()

    assert failed["state"] == "failed"
    assert prepare_formal_preview_attempt(settings=settings, job=job) is None
    with connect(settings.database_path, 5000) as conn:
        current_job = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job["id"],)
        ).fetchone()
    assert current_job["status"] == "failed"
    assert current_job["error_message"] == "formal_preview_render_failed"
    assert current_job["claimed_at"] is None


def test_worker_dispatches_generation_job_only_to_formal_processor(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    with connect(settings.database_path, 5000) as conn:
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        conn.execute("UPDATE assets SET preview_generation = 1 WHERE id = 1")
        insert_or_return_job(
            conn,
            job_type="preview",
            asset_id=1,
            payload_json=json.dumps(
                {
                    "asset_id": 1,
                    "preview_generation": 1,
                    "detection_required": True,
                }
            ),
            dedup_key="formal-preview",
            preview_generation=1,
        )
        conn.commit()
    monkeypatch.setenv("MEDIA_ROOT", str(settings.media_root))
    monkeypatch.setenv("API_TOKEN", settings.api_token)
    monkeypatch.setenv("DATABASE_PATH", str(settings.database_path))
    calls = []

    def record_formal(**kwargs):
        calls.append(kwargs["job"])
        return True

    monkeypatch.setattr(
        "app.workers.worker.process_formal_preview_job",
        record_formal,
    )
    monkeypatch.setattr(
        "app.workers.worker.process_preview_job",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("formal job reached legacy preview processor")
        ),
    )

    assert run_once() is True
    assert calls[0]["preview_generation"] == 1


@pytest.mark.parametrize(
    ("status", "is_log", "filename", "requested", "transform_status"),
    [
        ("apple_log", 0, "ordinary.mp4", "generated-apple-log-rec709", "unavailable"),
        ("apple_log", 1, "legacy-log.mov", "generated-apple-log-rec709", "unavailable"),
        ("not_log", 1, "misleading-log.mov", "compress-only", "not_requested"),
        ("unknown", 0, "unknown.bin", "compress-only", "not_requested"),
    ],
)
def test_formal_processor_uses_detector_not_legacy_hints_and_finalizes(
    tmp_path,
    monkeypatch,
    status,
    is_log,
    filename,
    requested,
    transform_status,
):
    settings = _settings(tmp_path)
    job = _claimed_formal_job(
        settings,
        payload={
            "asset_id": 1,
            "preview_generation": 1,
            "detection_required": True,
        },
    )
    _prepare_verified_original(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "UPDATE assets SET is_log = ?, filename = ? WHERE id = 1",
            (is_log, filename),
        )
        conn.commit()

    commands = _run_formal_success(
        settings, job, status=status, monkeypatch=monkeypatch
    )

    with connect(settings.database_path, 5000) as conn:
        attempt = dict(
            conn.execute("SELECT * FROM formal_preview_attempts").fetchone()
        )
        asset = dict(conn.execute("SELECT * FROM assets WHERE id = 1").fetchone())
        current_job = dict(
            conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        )
        provenance = dict(conn.execute("SELECT * FROM preview_provenance").fetchone())
        result = dict(
            conn.execute(
                "SELECT * FROM processed_results WHERE id = ?",
                (asset["formal_preview_id"],),
            ).fetchone()
        )
    assert attempt["state"] == "ready"
    assert attempt["requested_preset_id"] == requested
    assert attempt["color_transform_status"] == transform_status
    assert provenance["detection_status"] == status
    assert provenance["requested_preset_id"] == requested
    assert provenance["applied_preset_id"] == "compress-only"
    assert asset["log_detection_status"] == status
    assert asset["preview_status"] == "preview_ready"
    assert asset["formal_preview_id"] == result["id"]
    assert asset["active_processed_result_id"] == result["id"]
    assert result["preview_generation"] == 1
    assert current_job["status"] == "done"
    assert current_job["claimed_at"] is None
    command = commands[0]
    assert "libx264" in command
    assert "aac" in command
    assert "lut3d" not in command[command.index("-vf") + 1]
    assert "min(1080,ih)" in command[command.index("-vf") + 1]


def test_apple_log_registered_invalid_fails_without_rendering(tmp_path, monkeypatch):
    base = _settings(tmp_path)
    user_lut_root = tmp_path / "user-luts"
    (user_lut_root / "generated-apple-log-rec709").mkdir(parents=True)
    settings = replace(base, user_lut_root=user_lut_root)
    job = _claimed_formal_job(
        settings,
        payload={
            "asset_id": 1,
            "preview_generation": 1,
            "detection_required": True,
        },
    )
    _prepare_verified_original(settings)
    write_detector_artifacts(settings.detector_root)
    monkeypatch.setattr(
        "app.services.formal_preview_processing.read_ffprobe_version",
        lambda **_kwargs: "ffprobe test pinned",
    )

    assert process_formal_preview_job(
        settings=settings,
        job=job,
        probe_runner=lambda _path, _manifest: _detection("apple_log"),
        render_runner=lambda _command: pytest.fail("render must not run"),
    )

    with connect(settings.database_path, 5000) as conn:
        attempt = conn.execute("SELECT * FROM formal_preview_attempts").fetchone()
        asset = conn.execute("SELECT * FROM assets WHERE id = 1").fetchone()
        current_job = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job["id"],)
        ).fetchone()
        derived_count = conn.execute("SELECT COUNT(*) FROM derived_files").fetchone()[0]
    assert attempt["state"] == "failed"
    assert attempt["failure_code"] == "lut_preset_registered_invalid"
    assert asset["preview_status"] == "failed"
    assert asset["formal_preview_id"] is None
    assert current_job["status"] == "failed"
    assert derived_count == 0


def test_render_failure_is_terminal_and_removes_candidate(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    job = _claimed_formal_job(
        settings,
        payload={
            "asset_id": 1,
            "preview_generation": 1,
            "detection_required": True,
        },
    )
    _prepare_verified_original(settings)
    write_detector_artifacts(settings.detector_root)
    monkeypatch.setattr(
        "app.services.formal_preview_processing.read_ffprobe_version",
        lambda **_kwargs: "ffprobe test pinned",
    )

    def fail_render(_command):
        raise __import__(
            "app.services.ffmpeg", fromlist=["PreviewGenerationError"]
        ).PreviewGenerationError("raw failure")

    assert process_formal_preview_job(
        settings=settings,
        job=job,
        probe_runner=lambda _path, _manifest: _detection("not_log"),
        render_runner=fail_render,
    )

    with connect(settings.database_path, 5000) as conn:
        attempt = conn.execute("SELECT * FROM formal_preview_attempts").fetchone()
        current_job = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job["id"],)
        ).fetchone()
    assert attempt["failure_code"] == "formal_preview_render_failed"
    assert current_job["error_message"] == "formal_preview_render_failed"
    assert not (
        settings.media_root
        / "tmp"
        / "formal-previews"
        / attempt["id"]
        / "candidate.mp4"
    ).exists()


def test_rendering_recovery_uses_persisted_snapshot_without_registry_resolution(
    tmp_path,
):
    settings = _settings(tmp_path)
    job = _claimed_formal_job(
        settings,
        payload={
            "asset_id": 1,
            "preview_generation": 1,
            "detection_required": True,
        },
    )
    _prepare_verified_original(settings)
    attempt = prepare_formal_preview_attempt(settings=settings, job=job)
    detection = _detection("apple_log")
    snapshot, transform_status, transform_error = _resolve_preset(
        settings=settings,
        detection_status="apple_log",
    )
    with connect(settings.database_path, 5000) as conn:
        attempt = save_detection_snapshot(
            conn,
            attempt_id=attempt["id"],
            detection_status="apple_log",
            source_profile=None,
            detector_rule_version="test-v1",
            detector_manifest_sha256="a" * 64,
            detector_evidence_sha256=detection.evidence_sha256,
            detector_evidence_json=detection.evidence_json,
        )
        attempt = save_preset_snapshot(
            conn,
            attempt_id=attempt["id"],
            snapshot=snapshot,
            transform_kind="none",
            color_transform_status=transform_status,
            color_transform_error_code=transform_error,
        )
        conn.commit()
    assert attempt["state"] == "rendering"

    user_lut_root = tmp_path / "late-registry"
    (user_lut_root / "generated-apple-log-rec709").mkdir(parents=True)
    recovered_settings = replace(settings, user_lut_root=user_lut_root)

    def render(command):
        __import__("pathlib").Path(command[-1]).write_bytes(b"recovered-preview")

    assert process_formal_preview_job(
        settings=recovered_settings,
        job=job,
        probe_runner=lambda _path, _manifest: pytest.fail(
            "persisted detection must not be repeated"
        ),
        render_runner=render,
    )

    with connect(settings.database_path, 5000) as conn:
        recovered = conn.execute(
            "SELECT * FROM formal_preview_attempts WHERE id = ?", (attempt["id"],)
        ).fetchone()
    assert recovered["state"] == "ready"
    assert recovered["registry_classification"] == "absent"
    assert recovered["applied_preset_id"] == "compress-only"
