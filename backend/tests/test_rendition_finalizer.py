from pathlib import Path

import pytest

from app.db.connection import connect
from app.repositories.jobs import claim_next_job
from app.repositories.renditions import (
    get_rendition,
    restart_rendition_validation_in_transaction,
    transition_rendition,
)
from app.services.rendition_creation import create_rendition
from app.services.rendition_finalizer import (
    RenditionFinalizationError,
    TransformEvidence,
    finalize_rendition_output,
)
from app.services.rendition_processing import process_rendition_job
from app.services.storage import (
    generate_rendition_candidate_path,
    generate_rendition_relative_path,
    promote_rendition_candidate,
)
from tests.test_rendition_processing import environment, fake_ffmpeg_output


EVIDENCE = TransformEvidence(
    applied_preset_id="compress-only",
    transform_kind="none",
    color_transform_status="not_requested",
    color_transform_error_code=None,
)


def create_finalizing(settings, asset_id, *, client_id):
    result = create_rendition(
        settings=settings,
        asset_id=asset_id,
        client_request_id=client_id,
        preset_id="compress-only",
    )
    rendition_id = result.representation["rendition_id"]
    with connect(settings.database_path, 5000) as conn:
        rendition = get_rendition(conn, rendition_id)
        restart_rendition_validation_in_transaction(conn, rendition_id=rendition_id)
        transition_rendition(conn, rendition_id=rendition_id, new_state="rendering")
        transition_rendition(conn, rendition_id=rendition_id, new_state="finalizing")
        conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (rendition["job_id"],))
        conn.commit()
    candidate = generate_rendition_candidate_path(settings.media_root, rendition_id)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(f"rendered-{client_id}".encode())
    return rendition_id, rendition["job_id"], candidate


@pytest.mark.parametrize("completion_order", [("a", "b"), ("b", "a")])
def test_latest_generation_is_active_in_both_completion_orders(tmp_path, completion_order):
    settings, asset = environment(tmp_path)
    a = create_finalizing(settings, asset["id"], client_id="a" * 32)
    b = create_finalizing(settings, asset["id"], client_id="b" * 32)
    by_name = {"a": a, "b": b}
    original_active = None
    with connect(settings.database_path, 5000) as conn:
        original_active = conn.execute(
            "SELECT active_processed_result_id FROM assets WHERE id = ?", (asset["id"],)
        ).fetchone()[0]

    for name in completion_order:
        rendition_id, job_id, candidate = by_name[name]
        finalize_rendition_output(
            settings=settings,
            job_id=job_id,
            rendition_id=rendition_id,
            candidate_path=candidate,
            evidence=EVIDENCE,
        )

    with connect(settings.database_path, 5000) as conn:
        rows = {
            row["client_request_id"]: dict(row)
            for row in conn.execute("SELECT * FROM renditions").fetchall()
        }
        current_asset = dict(
            conn.execute("SELECT * FROM assets WHERE id = ?", (asset["id"],)).fetchone()
        )
        results = {
            row["id"]: dict(row) for row in conn.execute("SELECT * FROM processed_results").fetchall()
        }
        provenance_count = conn.execute("SELECT COUNT(*) FROM rendition_provenance").fetchone()[0]

    assert rows["a" * 32]["state"] == "superseded"
    assert rows["b" * 32]["state"] == "ready"
    assert current_asset["active_processed_result_id"] == rows["b" * 32]["result_id"]
    assert results[rows["a" * 32]["result_id"]]["status"] == "superseded"
    assert results[rows["b" * 32]["result_id"]]["status"] == "ready"
    assert results[rows["a" * 32]["result_id"]]["preview_generation"] is None
    assert results[rows["b" * 32]["result_id"]]["preview_generation"] is None
    assert results[original_active]["status"] == "superseded"
    assert provenance_count == 2
    assert (settings.media_root / generate_rendition_relative_path(a[0])).read_bytes() == (
        f"rendered-{'a' * 32}".encode()
    )
    assert current_asset["preview_status"] == "preview_ready"
    assert current_asset["review_status"] == "not_reviewed"
    assert current_asset["delete_candidate_status"] == "not_candidate"


@pytest.mark.parametrize(
    "failure_step",
    [
        "after_derived_file",
        "after_result",
        "after_provenance",
        "after_active_pointer",
        "after_rendition",
        "after_job",
    ],
)
def test_finalizer_write_failures_rollback_and_remove_promoted_output(tmp_path, failure_step):
    settings, asset = environment(tmp_path)
    rendition_id, job_id, candidate = create_finalizing(
        settings, asset["id"], client_id="a" * 32
    )
    with connect(settings.database_path, 5000) as conn:
        before_pointer = conn.execute(
            "SELECT active_processed_result_id FROM assets WHERE id = ?", (asset["id"],)
        ).fetchone()[0]
        base_result_count = conn.execute("SELECT COUNT(*) FROM processed_results").fetchone()[0]

    def fail(step):
        if step == failure_step:
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        finalize_rendition_output(
            settings=settings,
            job_id=job_id,
            rendition_id=rendition_id,
            candidate_path=candidate,
            evidence=EVIDENCE,
            fault_injector=fail,
        )

    with connect(settings.database_path, 5000) as conn:
        assert conn.execute("SELECT COUNT(*) FROM derived_files WHERE kind = 'rendition'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM rendition_provenance").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM processed_results").fetchone()[0] == base_result_count
        assert conn.execute(
            "SELECT active_processed_result_id FROM assets WHERE id = ?", (asset["id"],)
        ).fetchone()[0] == before_pointer
        assert conn.execute("SELECT state FROM renditions WHERE id = ?", (rendition_id,)).fetchone()[0] == "finalizing"
    assert not (settings.media_root / generate_rendition_relative_path(rendition_id)).exists()


def test_finalizer_rejects_lost_eligibility_and_preserves_existing_pointer(tmp_path):
    settings, asset = environment(tmp_path)
    rendition_id, job_id, candidate = create_finalizing(
        settings, asset["id"], client_id="a" * 32
    )
    with connect(settings.database_path, 5000) as conn:
        pointer = conn.execute(
            "SELECT active_processed_result_id FROM assets WHERE id = ?", (asset["id"],)
        ).fetchone()[0]
        conn.execute("UPDATE assets SET preview_status = 'failed' WHERE id = ?", (asset["id"],))
        conn.commit()

    with pytest.raises(RenditionFinalizationError) as error:
        finalize_rendition_output(
            settings=settings,
            job_id=job_id,
            rendition_id=rendition_id,
            candidate_path=candidate,
            evidence=EVIDENCE,
        )

    assert error.value.code == "rendition_asset_not_eligible"
    with connect(settings.database_path, 5000) as conn:
        current = conn.execute(
            "SELECT active_processed_result_id, review_status FROM assets WHERE id = ?", (asset["id"],)
        ).fetchone()
        assert current["active_processed_result_id"] == pointer
        assert current["review_status"] == "not_reviewed"
        assert conn.execute("SELECT COUNT(*) FROM rendition_provenance").fetchone()[0] == 0
    assert not (settings.media_root / generate_rendition_relative_path(rendition_id)).exists()


def test_rendition_storage_paths_are_backend_generated_and_non_overwriting(tmp_path):
    settings, _asset = environment(tmp_path)
    first = generate_rendition_candidate_path(settings.media_root, "a" * 32)
    second = generate_rendition_candidate_path(settings.media_root, "a" * 32)

    assert first != second
    assert str(first).startswith(str(settings.media_root / "tmp/renditions"))
    assert generate_rendition_relative_path("a" * 32) == f"previews/renditions/{'a' * 32}.mp4"


def test_lease_recovery_cleans_uncommitted_promoted_output_and_finishes(
    monkeypatch, tmp_path
):
    settings, asset = environment(tmp_path)
    rendition_id, job_id, candidate = create_finalizing(
        settings, asset["id"], client_id="a" * 32
    )
    _relative_path, final_path = promote_rendition_candidate(
        settings.media_root,
        candidate_path=candidate,
        rendition_id=rendition_id,
    )
    assert final_path.read_bytes() == f"rendered-{'a' * 32}".encode()
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'queued', claimed_at = NULL, lease_expires_at = NULL "
            "WHERE id = ?",
            (job_id,),
        )
        conn.commit()
        recovered = claim_next_job(conn, 30, {"rendition"})
    monkeypatch.setattr(
        "app.services.rendition_processing.run_ffmpeg",
        fake_ffmpeg_output(b"recovered render"),
    )

    process_rendition_job(settings=settings, job=recovered)

    with connect(settings.database_path, 5000) as conn:
        rendition = dict(
            conn.execute("SELECT * FROM renditions WHERE id = ?", (rendition_id,)).fetchone()
        )
        job = dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
    assert rendition["state"] == "ready"
    assert rendition["error_code"] is None
    assert job["status"] == "done"
    assert final_path.read_bytes() == b"recovered render"
