import hashlib
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.jobs import claim_next_job, insert_job, recover_expired_jobs
from app.services.preset_manifest import manifest_document_with_digest
from app.services.rendition_creation import create_rendition
from app.services.rendition_processing import process_rendition_job
from app.services.ffmpeg import build_video_preview_command, run_ffmpeg
from app.services.storage import initialize_storage
from scripts.generate_test_luts import generate_cube_bytes
from tests.test_rendition_api import seed_eligible_asset


def environment(tmp_path, *, user_lut_root=None):
    settings = Settings(
        media_root=tmp_path / "media",
        api_token="secret",
        database_path=tmp_path / "db.sqlite3",
        user_lut_root=user_lut_root,
    )
    initialize_storage(settings.media_root)
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)
        asset = seed_eligible_asset(conn, settings.media_root)
        conn.commit()
    return settings, asset


def create_and_claim(settings, asset_id, *, preset_id="compress-only", client_id="c" * 32):
    created = create_rendition(
        settings=settings,
        asset_id=asset_id,
        client_request_id=client_id,
        preset_id=preset_id,
    )
    with connect(settings.database_path, 5000) as conn:
        job = claim_next_job(conn, 30, {"rendition"})
    assert job is not None
    return created.representation, job


def fake_ffmpeg_output(content=b"rendered video"):
    def run(command):
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)

    return run


def write_custom(root, preset_id="custom-look"):
    candidate = root / preset_id
    candidate.mkdir(parents=True)
    cube = generate_cube_bytes(preset_id=preset_id, transform="identity")
    cube_path = candidate / "look.cube"
    cube_path.write_bytes(cube)
    manifest = {
        "schema_version": 1,
        "preset_id": preset_id,
        "display_name": "Custom look",
        "enabled": True,
        "preset_kind": "custom",
        "version": "1",
        "source_reference": "Internal source",
        "terms_reference": "Internal terms",
        "target_color_space": "Declared target",
        "lut_relative_path": "look.cube",
        "lut_sha256": hashlib.sha256(cube).hexdigest(),
        "file_format": "cube",
        "grid_size": 17,
    }
    (candidate / "manifest.json").write_bytes(manifest_document_with_digest(manifest))
    return cube_path


def load_outcome(settings, rendition_id, job_id):
    with connect(settings.database_path, 5000) as conn:
        rendition = dict(conn.execute("SELECT * FROM renditions WHERE id = ?", (rendition_id,)).fetchone())
        job = dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
        asset = dict(conn.execute("SELECT * FROM assets WHERE id = ?", (rendition["asset_id"],)).fetchone())
        provenance = conn.execute(
            "SELECT * FROM rendition_provenance WHERE rendition_id = ?", (rendition_id,)
        ).fetchone()
        result_count = conn.execute(
            "SELECT COUNT(*) FROM processed_results WHERE id = ?", (rendition["result_id"],)
        ).fetchone()[0] if rendition["result_id"] else 0
    return rendition, job, asset, dict(provenance) if provenance else None, result_count


def test_compress_only_finishes_ready_without_transform(monkeypatch, tmp_path):
    settings, asset = environment(tmp_path)
    rendition, job = create_and_claim(settings, asset["id"])
    monkeypatch.setattr(
        "app.services.rendition_processing.run_ffmpeg", fake_ffmpeg_output()
    )

    assert process_rendition_job(settings=settings, job=job) is True

    row, job_row, updated_asset, provenance, result_count = load_outcome(
        settings, rendition["rendition_id"], job["id"]
    )
    assert row["state"] == "ready"
    assert row["color_transform_status"] == "not_requested"
    assert row["applied_preset_id"] == "compress-only"
    assert job_row["status"] == "done"
    assert updated_asset["active_processed_result_id"] == row["result_id"]
    assert updated_asset["preview_status"] == "preview_ready"
    assert updated_asset["review_status"] == "not_reviewed"
    assert provenance["transform_kind"] == "none"
    assert provenance["color_transform_status"] == "not_requested"
    assert result_count == 1
    assert not (settings.media_root / "jobs" / rendition["rendition_id"]).exists()


def test_absent_preset_succeeds_as_explicit_unavailable_fallback(monkeypatch, tmp_path):
    settings, asset = environment(tmp_path)
    rendition, job = create_and_claim(settings, asset["id"], preset_id="missing-look")
    monkeypatch.setattr(
        "app.services.rendition_processing.run_ffmpeg", fake_ffmpeg_output()
    )

    process_rendition_job(settings=settings, job=job)

    row, _job, _asset, provenance, _count = load_outcome(
        settings, rendition["rendition_id"], job["id"]
    )
    assert row["state"] == "ready"
    assert row["applied_preset_id"] == "compress-only"
    assert row["color_transform_status"] == "unavailable"
    assert row["error_code"] == "lut_preset_unavailable"
    assert provenance["color_transform_error_code"] == "lut_preset_unavailable"


@pytest.mark.parametrize("preset_id", ["identity-v1", "test-red-blue-swap-v1"])
def test_generated_lut_uses_verified_private_snapshot(monkeypatch, tmp_path, preset_id):
    settings, asset = environment(tmp_path)
    rendition, job = create_and_claim(settings, asset["id"], preset_id=preset_id)
    commands = []

    def run(command):
        commands.append(command)
        fake_ffmpeg_output()(command)

    monkeypatch.setattr("app.services.rendition_processing.run_ffmpeg", run)
    process_rendition_job(settings=settings, job=job)

    row, _job, _asset, provenance, _count = load_outcome(
        settings, rendition["rendition_id"], job["id"]
    )
    assert row["state"] == "ready"
    assert row["color_transform_status"] == "applied"
    assert provenance["lut_sha256"] == row["expected_lut_sha256"]
    filter_value = commands[0][commands[0].index("-vf") + 1]
    assert "lut3d=" in filter_value
    assert str(settings.media_root / "jobs") in filter_value


def test_valid_custom_lut_records_applied_provenance(monkeypatch, tmp_path):
    root = tmp_path / "custom"
    root.mkdir()
    write_custom(root)
    settings, asset = environment(tmp_path, user_lut_root=root)
    rendition, job = create_and_claim(settings, asset["id"], preset_id="custom-look")
    monkeypatch.setattr(
        "app.services.rendition_processing.run_ffmpeg", fake_ffmpeg_output()
    )

    process_rendition_job(settings=settings, job=job)

    row, _job, _asset, provenance, _count = load_outcome(
        settings, rendition["rendition_id"], job["id"]
    )
    assert row["state"] == "ready"
    assert row["applied_preset_id"] == "custom-look"
    assert provenance["transform_kind"] == "lut"
    assert provenance["target_color_space"] == "Declared target"


def test_registered_invalid_source_change_and_lut_application_are_terminal(monkeypatch, tmp_path):
    root = tmp_path / "custom"
    root.mkdir()
    broken = root / "broken-look"
    broken.mkdir()
    (broken / "manifest.json").write_text("{}", encoding="utf-8")
    settings, asset = environment(tmp_path, user_lut_root=root)
    invalid, invalid_job = create_and_claim(
        settings, asset["id"], preset_id="broken-look", client_id="b" * 32
    )
    process_rendition_job(settings=settings, job=invalid_job)
    invalid_row, _, _, invalid_provenance, invalid_results = load_outcome(
        settings, invalid["rendition_id"], invalid_job["id"]
    )
    assert invalid_row["error_code"] == "lut_preset_registered_invalid"
    assert invalid_provenance is None and invalid_results == 0

    source = write_custom(root)
    changed, changed_job = create_and_claim(
        settings, asset["id"], preset_id="custom-look", client_id="d" * 32
    )
    source.write_bytes(b"changed")
    process_rendition_job(settings=settings, job=changed_job)
    changed_row, _, _, changed_provenance, changed_results = load_outcome(
        settings, changed["rendition_id"], changed_job["id"]
    )
    assert changed_row["error_code"] == "lut_preset_source_changed"
    assert changed_provenance is None and changed_results == 0

    source = write_custom(root, "reject-look")
    rejected, rejected_job = create_and_claim(
        settings, asset["id"], preset_id="reject-look", client_id="e" * 32
    )
    from app.services.ffmpeg import PreviewGenerationError

    monkeypatch.setattr(
        "app.services.rendition_processing.run_ffmpeg",
        lambda _command: (_ for _ in ()).throw(PreviewGenerationError("safe")),
    )
    process_rendition_job(settings=settings, job=rejected_job)
    rejected_row, _, _, rejected_provenance, rejected_results = load_outcome(
        settings, rejected["rendition_id"], rejected_job["id"]
    )
    assert rejected_row["error_code"] == "lut_application_failed"
    assert rejected_provenance is None and rejected_results == 0


def test_missing_and_mismatched_relation_fail_without_touching_asset(tmp_path):
    settings, asset = environment(tmp_path)
    with connect(settings.database_path, 5000) as conn:
        missing_job = insert_job(
            conn,
            job_type="rendition",
            asset_id=asset["id"],
            payload_json='{"rendition_id":"' + "f" * 32 + '"}',
        )
        conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (missing_job["id"],))
        before = tuple(
            conn.execute(
                "SELECT active_processed_result_id, preview_status, review_status FROM assets WHERE id = ?",
                (asset["id"],),
            ).fetchone()
        )
        conn.commit()
    process_rendition_job(settings=settings, job={**missing_job, "status": "running"})
    with connect(settings.database_path, 5000) as conn:
        assert conn.execute("SELECT status FROM jobs WHERE id = ?", (missing_job["id"],)).fetchone()[0] == "failed"
        assert tuple(
            conn.execute(
                "SELECT active_processed_result_id, preview_status, review_status FROM assets WHERE id = ?",
                (asset["id"],),
            ).fetchone()
        ) == before

    rendition, job = create_and_claim(settings, asset["id"])
    bad_job = {**job, "payload_json": '{"rendition_id":"' + "0" * 32 + '"}'}
    process_rendition_job(settings=settings, job=bad_job)
    row, job_row, updated, provenance, count = load_outcome(
        settings, rendition["rendition_id"], job["id"]
    )
    assert row["error_code"] == "rendition_relation_invalid"
    assert job_row["error_message"] == "rendition_relation_invalid"
    assert tuple(
        updated[key] for key in ("active_processed_result_id", "preview_status", "review_status")
    ) == before
    assert provenance is None and count == 0


def test_terminal_lease_recovery_reconciles_job_without_render(monkeypatch, tmp_path):
    settings, asset = environment(tmp_path)
    rendition, job = create_and_claim(settings, asset["id"])
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            """
            UPDATE renditions SET state = 'failed', color_transform_status = 'failed',
                error_code = 'rendition_storage_failed', terminal_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (rendition["rendition_id"],),
        )
        conn.execute(
            "UPDATE jobs SET status = 'queued', claimed_at = NULL, lease_expires_at = NULL WHERE id = ?",
            (job["id"],),
        )
        conn.commit()
        recovered = claim_next_job(conn, 30, {"rendition"})
    snapshot_dir = settings.media_root / "jobs" / rendition["rendition_id"]
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "lut.cube").write_bytes(b"left after committed failure")
    render = pytest.fail
    monkeypatch.setattr("app.services.rendition_processing.run_ffmpeg", render)

    process_rendition_job(settings=settings, job=recovered)

    _row, job_row, _asset, _provenance, _count = load_outcome(
        settings, rendition["rendition_id"], job["id"]
    )
    assert job_row["status"] == "failed"
    assert job_row["error_message"] == "rendition_storage_failed"
    assert not snapshot_dir.exists()


def test_ready_terminal_lease_recovery_marks_job_done_without_render(monkeypatch, tmp_path):
    settings, asset = environment(tmp_path)
    rendition, job = create_and_claim(settings, asset["id"])
    monkeypatch.setattr(
        "app.services.rendition_processing.run_ffmpeg", fake_ffmpeg_output()
    )
    process_rendition_job(settings=settings, job=job)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'queued', claimed_at = NULL, lease_expires_at = NULL WHERE id = ?",
            (job["id"],),
        )
        conn.commit()
        recovered = claim_next_job(conn, 30, {"rendition"})
    monkeypatch.setattr(
        "app.services.rendition_processing.run_ffmpeg",
        lambda _command: (_ for _ in ()).throw(AssertionError("terminal rerendered")),
    )

    process_rendition_job(settings=settings, job=recovered)

    row, job_row, _asset, _provenance, _count = load_outcome(
        settings, rendition["rendition_id"], job["id"]
    )
    assert row["state"] == "ready"
    assert job_row["status"] == "done"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_generated_identity_and_swap_luts_apply_to_synthetic_video(tmp_path):
    input_path = tmp_path / "red.mp4"
    identity_path = tmp_path / "identity.mp4"
    swapped_path = tmp_path / "swapped.mp4"
    preset_root = Settings(
        media_root=tmp_path / "media",
        api_token="secret",
        database_path=tmp_path / "db.sqlite3",
    ).built_in_preset_root
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=32x32:d=0.2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(input_path),
        ],
        check=True,
        capture_output=True,
    )
    run_ffmpeg(
        build_video_preview_command(
            input_path=input_path,
            output_path=identity_path,
            lut_path=preset_root / "identity-v1/identity-v1.cube",
        )
    )
    run_ffmpeg(
        build_video_preview_command(
            input_path=input_path,
            output_path=swapped_path,
            lut_path=preset_root / "test-red-blue-swap-v1/test-red-blue-swap-v1.cube",
        )
    )

    def first_rgb(path):
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
                "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ],
            check=True,
            capture_output=True,
        )
        return tuple(result.stdout[:3])

    identity_rgb = first_rgb(identity_path)
    swapped_rgb = first_rgb(swapped_path)
    assert identity_rgb[0] > identity_rgb[2] + 100
    assert swapped_rgb[2] > swapped_rgb[0] + 100
