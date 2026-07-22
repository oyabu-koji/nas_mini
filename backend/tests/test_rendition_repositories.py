import sqlite3

import pytest

from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.derived_files import insert_derived_file
from app.repositories.jobs import SUPPORTED_JOB_TYPES, claim_next_job, insert_job
from app.repositories.processed_results import (
    insert_ready_processed_result,
    insert_superseded_processed_result,
)
from app.repositories.rendition_provenance import insert_rendition_provenance
from app.repositories.renditions import (
    complete_rendition_in_transaction,
    fail_rendition_in_transaction,
    get_rendition,
    get_rendition_by_client_request,
    get_rendition_by_job,
    get_rendition_for_asset,
    increment_selection_generation,
    insert_rendition,
    serialize_rendition,
    transition_rendition,
)
from app.services.preset_manifest import compress_only_snapshot
from tests.test_renditions_migration import seed_asset_and_job


def insert_repository_rendition(conn, *, rendition_id="b" * 32, client_id="c" * 32):
    asset, job = seed_asset_and_job(conn)
    generation = increment_selection_generation(conn, asset_id=asset["id"])
    rendition = insert_rendition(
        conn,
        rendition_id=rendition_id,
        asset_id=asset["id"],
        client_request_id=client_id,
        job_id=job["id"],
        selection_generation=generation,
        snapshot=compress_only_snapshot(),
    )
    return asset, job, rendition


def test_rendition_repository_crud_and_safe_serialization_are_commitless(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        conn.execute("BEGIN IMMEDIATE")
        asset, job, rendition = insert_repository_rendition(conn)

        assert conn.in_transaction
        assert get_rendition(conn, rendition["id"]) == rendition
        assert get_rendition_for_asset(conn, asset_id=asset["id"], rendition_id=rendition["id"])
        assert get_rendition_for_asset(conn, asset_id=asset["id"] + 1, rendition_id=rendition["id"]) is None
        assert get_rendition_by_client_request(conn, "c" * 32)["id"] == rendition["id"]
        assert get_rendition_by_job(conn, job["id"])["id"] == rendition["id"]
        safe = serialize_rendition(rendition)
        assert "manifest_canonical_bytes" not in safe
        assert "source_relative_lut_path" not in safe
        conn.rollback()

        assert get_rendition(conn, rendition["id"]) is None


def test_rendition_repository_enforces_allowed_phase_transitions(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        _asset, _job, rendition = insert_repository_rendition(conn)
        for state in ("validating", "rendering", "finalizing"):
            rendition = transition_rendition(conn, rendition_id=rendition["id"], new_state=state)
            assert rendition["state"] == state

        fail_rendition_in_transaction(
            conn, rendition_id=rendition["id"], error_code="rendition_asset_not_eligible"
        )
        terminal = get_rendition(conn, rendition["id"])
        assert terminal["state"] == "failed"
        assert terminal["color_transform_status"] == "failed"


def test_ready_rendition_requires_matching_immutable_provenance(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        asset, _job, rendition = insert_repository_rendition(conn)
        for state in ("validating", "rendering", "finalizing"):
            rendition = transition_rendition(conn, rendition_id=rendition["id"], new_state=state)
        derived = insert_derived_file(
            conn,
            asset_id=asset["id"],
            kind="rendition",
            path="previews/renditions/output.mp4",
            mime_type="video/mp4",
            size_bytes=10,
        )
        result, _ = insert_ready_processed_result(
            conn,
            asset_id=asset["id"],
            derived_file_id=derived["id"],
            mime_type="video/mp4",
            size_bytes=10,
            sha256="d" * 64,
            result_id="e" * 32,
        )
        with pytest.raises(sqlite3.IntegrityError, match="provenance_missing"):
            complete_rendition_in_transaction(
                conn,
                rendition_id=rendition["id"],
                state="ready",
                result_id=result["id"],
                applied_preset_id="compress-only",
                color_transform_status="not_requested",
                error_code=None,
            )

        insert_rendition_provenance(
            conn,
            rendition=rendition,
            result_id=result["id"],
            derived_file_id=derived["id"],
            applied_preset_id="compress-only",
            transform_kind="none",
            color_transform_status="not_requested",
            color_transform_error_code=None,
        )
        complete_rendition_in_transaction(
            conn,
            rendition_id=rendition["id"],
            state="ready",
            result_id=result["id"],
            applied_preset_id="compress-only",
            color_transform_status="not_requested",
            error_code=None,
        )

        with pytest.raises(sqlite3.IntegrityError, match="terminal_rendition_is_immutable"):
            conn.execute("UPDATE renditions SET error_code = 'changed' WHERE id = ?", (rendition["id"],))
        with pytest.raises(sqlite3.IntegrityError, match="rendition_provenance_is_immutable"):
            conn.execute(
                "UPDATE rendition_provenance SET source_reference = 'changed' WHERE rendition_id = ?",
                (rendition["id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="delete_not_allowed"):
            conn.execute("DELETE FROM rendition_provenance WHERE rendition_id = ?", (rendition["id"],))


def test_provenance_rejects_wrong_result_derived_relation(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        asset, _job, rendition = insert_repository_rendition(conn)
        derived = insert_derived_file(
            conn,
            asset_id=asset["id"],
            kind="rendition",
            path="previews/renditions/output.mp4",
            mime_type="video/mp4",
            size_bytes=10,
        )
        preview = insert_derived_file(
            conn,
            asset_id=asset["id"],
            kind="preview",
            path="previews/base.mp4",
            mime_type="video/mp4",
            size_bytes=10,
        )
        result, _ = insert_ready_processed_result(
            conn,
            asset_id=asset["id"],
            derived_file_id=preview["id"],
            mime_type="video/mp4",
            size_bytes=10,
            sha256="d" * 64,
            result_id="e" * 32,
        )

        with pytest.raises(sqlite3.IntegrityError, match="relation_invalid"):
            insert_rendition_provenance(
                conn,
                rendition=rendition,
                result_id=result["id"],
                derived_file_id=derived["id"],
                applied_preset_id="compress-only",
                transform_kind="none",
                color_transform_status="not_requested",
                color_transform_error_code=None,
            )


def test_superseded_result_helper_keeps_preview_generation_null(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        asset, _job, _rendition = insert_repository_rendition(conn)
        derived = insert_derived_file(
            conn,
            asset_id=asset["id"],
            kind="rendition",
            path="previews/renditions/stale.mp4",
            mime_type="video/mp4",
            size_bytes=10,
        )
        result = insert_superseded_processed_result(
            conn,
            asset_id=asset["id"],
            derived_file_id=derived["id"],
            mime_type="video/mp4",
            size_bytes=10,
            sha256="f" * 64,
            result_id="e" * 32,
        )

    assert result["status"] == "superseded"
    assert result["preview_generation"] is None


def test_rendition_jobs_are_claimed_as_supported_work(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        asset, _job, _rendition = insert_repository_rendition(conn)
        claimed = claim_next_job(conn, 30, SUPPORTED_JOB_TYPES)

    assert claimed["job_type"] == "rendition"
    assert claimed["asset_id"] == asset["id"]
