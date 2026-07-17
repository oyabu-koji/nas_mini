import json

import pytest

from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.jobs import insert_or_return_job, requeue_retryable_finalize_job


def test_insert_or_return_job_uses_dedup_key(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        first, created = insert_or_return_job(
            conn,
            job_type="upload_finalize",
            asset_id=None,
            payload_json=json.dumps({"session_id": "session"}),
            dedup_key="finalize:session",
        )
        second, recovered = insert_or_return_job(
            conn,
            job_type="upload_finalize",
            asset_id=None,
            payload_json=json.dumps({"session_id": "session"}),
            dedup_key="finalize:session",
        )
        with pytest.raises(ValueError, match="conflicts"):
            insert_or_return_job(
                conn,
                job_type="preview",
                asset_id=None,
                payload_json="{}",
                dedup_key="finalize:session",
            )

    assert created is True
    assert recovered is False
    assert first["id"] == second["id"]


def test_retryable_finalize_job_can_be_requeued_but_other_jobs_cannot(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        job, _ = insert_or_return_job(
            conn,
            job_type="upload_finalize",
            asset_id=None,
            payload_json="{}",
            dedup_key="finalize:session",
        )
        conn.execute("UPDATE jobs SET status = 'failed' WHERE id = ?", (job["id"],))
        requeued = requeue_retryable_finalize_job(conn, job["id"])
        with pytest.raises(ValueError, match="not retryable"):
            requeue_retryable_finalize_job(conn, 999)

    assert requeued["status"] == "queued"
