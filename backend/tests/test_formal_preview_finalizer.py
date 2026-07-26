import hashlib
import pytest

from app.db.connection import connect
from app.repositories.formal_previews import (
    save_detection_snapshot,
    save_preset_snapshot,
    transition_formal_preview_attempt,
)
from app.services.canonical_json import canonical_json_bytes
from app.services.formal_preview_finalizer import (
    finalize_formal_preview_output,
    inspect_formal_preview_candidate,
)
from app.services.formal_preview_processing import prepare_formal_preview_attempt
from app.services.preset_manifest import compress_only_snapshot
from app.services.processed_result_delivery import resolve_deliverable_result_by_id
from app.services.rendition_finalizer import (
    TransformEvidence,
    finalize_rendition_output,
)
from app.services.storage import generate_formal_preview_candidate_path
from app.services.storage import (
    StorageError,
    cleanup_formal_preview_candidate,
    promote_formal_preview_candidate,
)
from tests.test_formal_preview_processing import (
    _claimed_formal_job,
    _prepare_verified_original,
    _run_formal_success,
    _settings,
)
from tests.test_rendition_finalizer import create_finalizing
from tests.test_phase2b_schema import _insert_ready_managed


def _finalizing_attempt(settings):
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
    assert attempt is not None
    evidence = canonical_json_bytes({"classification": "not_log", "values": []})
    with connect(settings.database_path, 5000) as conn:
        attempt = save_detection_snapshot(
            conn,
            attempt_id=attempt["id"],
            detection_status="not_log",
            source_profile=None,
            detector_rule_version="test-v1",
            detector_manifest_sha256="a" * 64,
            detector_evidence_sha256=hashlib.sha256(evidence).hexdigest(),
            detector_evidence_json=evidence,
        )
        attempt = save_preset_snapshot(
            conn,
            attempt_id=attempt["id"],
            snapshot=compress_only_snapshot(),
            transform_kind="none",
            color_transform_status="not_requested",
            color_transform_error_code=None,
        )
        attempt = transition_formal_preview_attempt(
            conn, attempt_id=attempt["id"], new_state="finalizing"
        )
        conn.commit()
    candidate = generate_formal_preview_candidate_path(
        settings.media_root, attempt["id"]
    )
    candidate.write_bytes(b"formal-preview")
    candidate.chmod(0o600)
    return job, attempt, candidate, inspect_formal_preview_candidate(candidate)


@pytest.mark.parametrize(
    "step",
    [
        "after_derived_file",
        "after_result",
        "after_attempt",
        "after_provenance",
        "after_asset",
        "after_job",
    ],
)
def test_finalizer_write_failure_rolls_back_all_database_changes(tmp_path, step):
    settings = _settings(tmp_path)
    job, attempt, candidate, identity = _finalizing_attempt(settings)

    def fail(current_step):
        if current_step == step:
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        finalize_formal_preview_output(
            settings=settings,
            job_id=job["id"],
            attempt_id=attempt["id"],
            candidate_path=candidate,
            candidate_identity=identity,
            fault_injector=fail,
        )

    with connect(settings.database_path, 5000) as conn:
        current_attempt = conn.execute(
            "SELECT * FROM formal_preview_attempts WHERE id = ?", (attempt["id"],)
        ).fetchone()
        current_job = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job["id"],)
        ).fetchone()
        asset = conn.execute("SELECT * FROM assets WHERE id = 1").fetchone()
        counts = tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("derived_files", "processed_results", "preview_provenance")
        )
    assert current_attempt["state"] == "finalizing"
    assert current_job["status"] == "running"
    assert asset["formal_preview_id"] is None
    assert asset["preview_status"] == "preview_generating"
    assert counts == (0, 0, 0)
    assert not candidate.exists()
    assert not (
        settings.media_root / "previews" / "formal" / f"{attempt['id']}.mp4"
    ).exists()


def test_finalizer_preserves_current_managed_authority(tmp_path):
    settings = _settings(tmp_path)
    job, attempt, candidate, identity = _finalizing_attempt(settings)
    managed_result_id = "b" * 32
    with connect(settings.database_path, 5000) as conn:
        _insert_ready_managed(
            conn,
            generation=1,
            result_id=managed_result_id,
            rendition_id="c" * 32,
        )
        conn.execute(
            "UPDATE assets SET active_processed_result_id = ? WHERE id = 1",
            (managed_result_id,),
        )
        failed_job_id = conn.execute(
            """
            INSERT INTO jobs (
                job_type, status, asset_id, payload_json, dedup_key
            ) VALUES ('rendition', 'failed', 1, '{}', 'newer-failed')
            """
        ).lastrowid
        conn.execute(
            """
            INSERT INTO renditions (
                id, asset_id, client_request_id, job_id,
                selection_generation, requested_preset_id,
                registry_classification, state, error_code, terminal_at
            ) VALUES (?, 1, ?, ?, 2, 'compress-only', 'valid',
                      'failed', 'rendition_storage_failed', CURRENT_TIMESTAMP)
            """,
            ("d" * 32, "2" * 32, failed_job_id),
        )
        conn.execute(
            "UPDATE assets SET rendition_selection_generation = 2 WHERE id = 1"
        )
        conn.commit()

    assert finalize_formal_preview_output(
        settings=settings,
        job_id=job["id"],
        attempt_id=attempt["id"],
        candidate_path=candidate,
        candidate_identity=identity,
    )

    with connect(settings.database_path, 5000) as conn:
        asset = conn.execute("SELECT * FROM assets WHERE id = 1").fetchone()
        managed = conn.execute(
            "SELECT * FROM processed_results WHERE id = ?", (managed_result_id,)
        ).fetchone()
        formal = conn.execute(
            "SELECT * FROM processed_results WHERE id = ?",
            (asset["formal_preview_id"],),
        ).fetchone()
    assert asset["active_processed_result_id"] == managed_result_id
    assert asset["formal_preview_id"] != managed_result_id
    assert managed["status"] == "ready"
    assert formal["status"] == "ready"
    assert formal["preview_generation"] == 1


def test_finalizer_converges_stale_generation_without_asset_mutation(tmp_path):
    settings = _settings(tmp_path)
    job, attempt, candidate, identity = _finalizing_attempt(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute("UPDATE assets SET preview_generation = 2 WHERE id = 1")
        conn.commit()

    assert (
        finalize_formal_preview_output(
            settings=settings,
            job_id=job["id"],
            attempt_id=attempt["id"],
            candidate_path=candidate,
            candidate_identity=identity,
        )
        is False
    )

    with connect(settings.database_path, 5000) as conn:
        current_attempt = conn.execute(
            "SELECT * FROM formal_preview_attempts WHERE id = ?", (attempt["id"],)
        ).fetchone()
        current_job = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job["id"],)
        ).fetchone()
        asset = conn.execute("SELECT * FROM assets WHERE id = 1").fetchone()
    assert current_attempt["state"] == "superseded"
    assert current_job["status"] == "failed"
    assert current_job["error_message"] == "preview_generation_superseded"
    assert current_job["claimed_at"] is None
    assert asset["preview_generation"] == 2
    assert asset["preview_status"] == "preview_generating"
    assert asset["formal_preview_id"] is None


def test_candidate_path_is_private_and_deterministic(tmp_path):
    settings = _settings(tmp_path)
    _job, attempt, candidate, _identity = _finalizing_attempt(settings)

    assert candidate.parent.stat().st_mode & 0o777 == 0o700
    assert candidate.stat().st_mode & 0o777 == 0o600
    assert candidate == generate_formal_preview_candidate_path(
        settings.media_root, attempt["id"]
    )


def test_promote_after_crash_recovers_same_candidate_identity(tmp_path):
    settings = _settings(tmp_path)
    job, attempt, candidate, identity = _finalizing_attempt(settings)
    _relative, final_path = promote_formal_preview_candidate(
        settings.media_root,
        candidate_path=candidate,
        attempt_id=attempt["id"],
    )
    assert candidate.exists()
    assert final_path.exists()

    assert finalize_formal_preview_output(
        settings=settings,
        job_id=job["id"],
        attempt_id=attempt["id"],
        candidate_path=candidate,
        candidate_identity=identity,
    )

    assert not candidate.exists()
    assert final_path.read_bytes() == b"formal-preview"


def test_candidate_cleanup_does_not_follow_symlink(tmp_path):
    settings = _settings(tmp_path)
    _job, attempt, candidate, _identity = _finalizing_attempt(settings)
    external = tmp_path / "external.mp4"
    external.write_bytes(b"must-remain")
    candidate.unlink()
    candidate.symlink_to(external)

    with pytest.raises(StorageError):
        cleanup_formal_preview_candidate(settings.media_root, attempt["id"])

    assert external.read_bytes() == b"must-remain"


def test_managed_switch_preserves_formal_and_exact_delivery(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    preview_job = _claimed_formal_job(
        settings,
        payload={
            "asset_id": 1,
            "preview_generation": 1,
            "detection_required": True,
        },
    )
    _prepare_verified_original(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute("UPDATE assets SET is_log = 1 WHERE id = 1")
        conn.commit()
    _run_formal_success(
        settings, preview_job, status="apple_log", monkeypatch=monkeypatch
    )
    with connect(settings.database_path, 5000) as conn:
        original_asset = dict(
            conn.execute("SELECT * FROM assets WHERE id = 1").fetchone()
        )
    formal_result_id = original_asset["formal_preview_id"]

    first = create_finalizing(settings, 1, client_id="d" * 32)
    finalize_rendition_output(
        settings=settings,
        job_id=first[1],
        rendition_id=first[0],
        candidate_path=first[2],
        evidence=TransformEvidence(
            applied_preset_id="compress-only",
            transform_kind="none",
            color_transform_status="not_requested",
            color_transform_error_code=None,
        ),
    )
    second = create_finalizing(settings, 1, client_id="e" * 32)
    finalize_rendition_output(
        settings=settings,
        job_id=second[1],
        rendition_id=second[0],
        candidate_path=second[2],
        evidence=TransformEvidence(
            applied_preset_id="compress-only",
            transform_kind="none",
            color_transform_status="not_requested",
            color_transform_error_code=None,
        ),
    )

    with connect(settings.database_path, 5000) as conn:
        asset = dict(conn.execute("SELECT * FROM assets WHERE id = 1").fetchone())
        formal = conn.execute(
            "SELECT * FROM processed_results WHERE id = ?", (formal_result_id,)
        ).fetchone()
        first_rendition = conn.execute(
            "SELECT * FROM renditions WHERE id = ?", (first[0],)
        ).fetchone()
        second_rendition = conn.execute(
            "SELECT * FROM renditions WHERE id = ?", (second[0],)
        ).fetchone()
        first_result = conn.execute(
            "SELECT * FROM processed_results WHERE id = ?",
            (first_rendition["result_id"],),
        ).fetchone()
        second_result = conn.execute(
            "SELECT * FROM processed_results WHERE id = ?",
            (second_rendition["result_id"],),
        ).fetchone()
        formal_delivery = resolve_deliverable_result_by_id(
            settings=settings,
            conn=conn,
            asset=asset,
            result_id=formal_result_id,
        )
        managed_delivery = resolve_deliverable_result_by_id(
            settings=settings,
            conn=conn,
            asset=asset,
            result_id=second_result["id"],
        )
    assert asset["formal_preview_id"] == formal_result_id
    assert asset["active_processed_result_id"] == second_result["id"]
    assert asset["preview_status"] == original_asset["preview_status"]
    assert asset["review_status"] == original_asset["review_status"]
    assert formal["status"] == "ready"
    assert first_result["status"] == "superseded"
    assert second_result["status"] == "ready"
    assert formal_delivery is not None
    assert managed_delivery is not None
