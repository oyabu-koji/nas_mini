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
from app.services.bounded_subprocess import BoundedProcessError
from app.services.canonical_json import canonical_json_bytes
from app.services.detector_v2_migration import apply_detector_v2_migration
from app.services.formal_preview_processing import (
    FormalPreviewProcessingError,
    _resolve_preset,
    parse_formal_preview_payload,
    prepare_formal_preview_attempt,
    process_formal_preview_job,
)
from app.services.phase2b_migration import apply_phase2b_migration
from app.services.phase2c_migration import apply_phase2c_migration
from app.workers.worker import run_once
from tests.detector_test_support import write_detector_artifacts
from tests.test_phase2b_schema import _insert_session_asset
from tests.test_preset_registry import write_custom


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


def _detection(status, source_profile="apple-log-1"):
    evidence = canonical_json_bytes({"classification": status, "values": []})
    return DetectionResult(
        status=status,
        source_profile=source_profile if status == "apple_log" else None,
        evidence_sha256=hashlib.sha256(evidence).hexdigest(),
        evidence_json=evidence,
    )


def _run_formal_success(
    settings,
    job,
    *,
    status,
    monkeypatch,
    source_profile="apple-log-1",
):
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
        probe_runner=lambda _path, _manifest: _detection(status, source_profile),
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


def test_formal_processor_passes_verified_database_size_to_default_detector(
    tmp_path, monkeypatch
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
    source_path = _prepare_verified_original(settings, content=b"database-sized")
    write_detector_artifacts(settings.detector_root)
    monkeypatch.setattr(
        "app.services.formal_preview_processing.read_ffprobe_version",
        lambda **_kwargs: "ffprobe test pinned",
    )
    captured = {}

    def detect(**kwargs):
        captured.update(kwargs)
        return _detection("not_log")

    def render(command):
        __import__("pathlib").Path(command[-1]).write_bytes(b"encoded-preview")

    monkeypatch.setattr(
        "app.services.formal_preview_processing.detect_path_same_fd",
        detect,
    )

    assert process_formal_preview_job(
        settings=settings,
        job=job,
        render_runner=render,
    )

    assert captured["path"] == source_path
    assert captured["expected_size"] == len(b"database-sized")


def test_apple_log_registered_invalid_guard_stops_before_processing(
    tmp_path,
    monkeypatch,
):
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

    from app.services.initial_release_guard import InitialReleaseConfigurationError

    with pytest.raises(InitialReleaseConfigurationError):
        process_formal_preview_job(
            settings=settings,
            job=job,
            probe_runner=lambda _path, _manifest: pytest.fail("probe must not run"),
            render_runner=lambda _command: pytest.fail("render must not run"),
        )

    with connect(settings.database_path, 5000) as conn:
        attempt = conn.execute("SELECT * FROM formal_preview_attempts").fetchone()
        asset = conn.execute("SELECT * FROM assets WHERE id = 1").fetchone()
        current_job = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job["id"],)
        ).fetchone()
        derived_count = conn.execute("SELECT COUNT(*) FROM derived_files").fetchone()[0]
    assert attempt is None
    assert asset["preview_status"] == "preview_generating"
    assert asset["formal_preview_id"] is None
    assert current_job["status"] == "running"
    assert derived_count == 0


def test_apple_log2_falls_back_to_profile_specific_requested_preset(
    tmp_path,
    monkeypatch,
):
    base_settings = _settings(tmp_path)
    built_in = tmp_path / "built-in"
    user = tmp_path / "user"
    built_in.mkdir()
    user.mkdir()
    settings = replace(
        base_settings,
        built_in_preset_root=built_in,
        user_lut_root=user,
    )
    content = b"video-source"
    digest = hashlib.sha256(content).hexdigest()
    relative_path = "originals/sessions/session-one.mov"
    original_path = settings.media_root / relative_path
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(content)
    with connect(settings.database_path, 5000) as conn:
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        conn.execute(
            """
            UPDATE assets
            SET original_path = ?, size_bytes = ?, server_sha256 = ?,
                preview_status = 'not_started'
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
    apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )
    apply_detector_v2_migration(
        settings=settings,
        mode="apply",
        offline_maintenance_confirmed=True,
        api_stopped_confirmed=True,
        release_040_ready_confirmed=True,
        release_readiness_check=lambda _settings: True,
    )
    payload = {
            "asset_id": 1,
            "preview_generation": 1,
            "detection_required": True,
    }
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            """
            UPDATE assets
            SET preview_generation = 1, preview_status = 'preview_generating'
            WHERE id = 1
            """
        )
        queued, _ = insert_or_return_job(
            conn,
            job_type="preview",
            asset_id=1,
            payload_json=json.dumps(payload, separators=(",", ":")),
            dedup_key="formal-preview",
            preview_generation=1,
        )
        conn.commit()
        job = claim_next_job(conn, 300, {"preview"})
    assert job is not None and job["id"] == queued["id"]

    commands = _run_formal_success(
        settings,
        job,
        status="apple_log",
        source_profile="apple-log-2",
        monkeypatch=monkeypatch,
    )

    with connect(settings.database_path, 5000) as conn:
        attempt = dict(conn.execute("SELECT * FROM formal_preview_attempts").fetchone())
        provenance = dict(conn.execute("SELECT * FROM preview_provenance").fetchone())
        asset = dict(conn.execute("SELECT * FROM assets WHERE id = 1").fetchone())
    for values in (attempt, provenance):
        assert values["source_profile"] == "apple-log-2"
        assert values["requested_preset_id"] == "generated-apple-log2-rec709"
        assert values["applied_preset_id"] == "compress-only"
        assert values["transform_kind"] == "none"
        assert values["color_transform_status"] == "unavailable"
        assert values["color_transform_error_code"] == "lut_preset_unavailable"
    assert asset["source_profile"] == "apple-log-2"
    assert asset["preview_status"] == "preview_ready"
    assert "lut3d" not in commands[0][commands[0].index("-vf") + 1]


@pytest.mark.parametrize(
    "error_code",
    [
        "log_container_invalid",
        "log_container_resource_limit",
        "log_container_source_changed",
    ],
)
def test_container_detection_failure_is_terminal_without_derived_output(
    tmp_path,
    monkeypatch,
    error_code,
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
    write_detector_artifacts(settings.detector_root)
    monkeypatch.setattr(
        "app.services.formal_preview_processing.read_ffprobe_version",
        lambda **_kwargs: "ffprobe test pinned",
    )

    assert process_formal_preview_job(
        settings=settings,
        job=job,
        probe_runner=lambda _path, _manifest: (_ for _ in ()).throw(
            BoundedProcessError(error_code)
        ),
        render_runner=lambda _command: pytest.fail("render must not run"),
    )

    with connect(settings.database_path, 5000) as conn:
        attempt = dict(conn.execute("SELECT * FROM formal_preview_attempts").fetchone())
        asset = dict(conn.execute("SELECT * FROM assets WHERE id = 1").fetchone())
        current_job = dict(
            conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        )
        derived_count = conn.execute("SELECT COUNT(*) FROM derived_files").fetchone()[0]
        result_count = conn.execute("SELECT COUNT(*) FROM processed_results").fetchone()[0]
    assert attempt["state"] == "failed"
    assert attempt["failure_code"] == error_code
    assert current_job["status"] == "failed"
    assert current_job["error_message"] == error_code
    assert asset["preview_status"] == "failed"
    assert asset["formal_preview_id"] is None
    assert derived_count == 0
    assert result_count == 0


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
        source_profile="apple-log-1",
    )
    with connect(settings.database_path, 5000) as conn:
        attempt = save_detection_snapshot(
            conn,
            attempt_id=attempt["id"],
            detection_status="apple_log",
            source_profile="apple-log-1",
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
    user_lut_root.mkdir()
    write_custom(
        user_lut_root,
        "generated-apple-log-rec709",
        enabled=False,
    )
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
