import pytest

from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.formal_previews import (
    FormalPreviewRepositoryError,
    get_formal_preview_attempt,
    insert_or_get_formal_preview_attempt,
    save_detection_snapshot,
    transition_formal_preview_attempt,
)
from tests.test_phase2b_schema import _insert_session_asset, apply_phase2b_schema


def test_formal_attempt_repository_is_commitless_and_reuses_job_identity(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        apply_phase2b_schema(conn)
        conn.execute("UPDATE assets SET preview_generation = 1 WHERE id = 1")
        job_id = conn.execute(
            """
            INSERT INTO jobs (
                job_type, status, asset_id, payload_json, dedup_key,
                preview_generation
            ) VALUES ('preview', 'running', 1, '{}', 'formal-preview', 1)
            """
        ).lastrowid
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")

        attempt, created = insert_or_get_formal_preview_attempt(
            conn,
            asset_id=1,
            job_id=job_id,
            preview_generation=1,
            attempt_id="1" * 32,
        )
        same, created_again = insert_or_get_formal_preview_attempt(
            conn,
            asset_id=1,
            job_id=job_id,
            preview_generation=1,
        )

        assert created is True
        assert created_again is False
        assert same["id"] == attempt["id"]
        assert conn.in_transaction
        conn.rollback()
        assert get_formal_preview_attempt(conn, attempt_id=attempt["id"]) is None


def test_formal_attempt_repository_enforces_transitions_and_detection_snapshot(
    tmp_path,
):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        apply_phase2b_schema(conn)
        conn.execute("UPDATE assets SET preview_generation = 1 WHERE id = 1")
        job_id = conn.execute(
            """
            INSERT INTO jobs (
                job_type, status, asset_id, payload_json, dedup_key,
                preview_generation
            ) VALUES ('preview', 'running', 1, '{}', 'formal-preview', 1)
            """
        ).lastrowid
        attempt, _ = insert_or_get_formal_preview_attempt(
            conn,
            asset_id=1,
            job_id=job_id,
            preview_generation=1,
            attempt_id="1" * 32,
        )
        transition_formal_preview_attempt(
            conn, attempt_id=attempt["id"], new_state="probing"
        )
        detected = save_detection_snapshot(
            conn,
            attempt_id=attempt["id"],
            detection_status="unknown",
            source_profile=None,
            detector_rule_version="rule-v1",
            detector_manifest_sha256="a" * 64,
            detector_evidence_sha256="b" * 64,
            detector_evidence_json=b'{"classification":"unknown","values":[]}',
        )

        assert detected["state"] == "resolving"
        with pytest.raises(FormalPreviewRepositoryError):
            transition_formal_preview_attempt(
                conn, attempt_id=attempt["id"], new_state="ready"
            )
        failed = transition_formal_preview_attempt(
            conn,
            attempt_id=attempt["id"],
            new_state="failed",
            failure_code="formal_preview_render_failed",
        )
        assert failed["state"] == "failed"
