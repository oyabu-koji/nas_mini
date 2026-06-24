from datetime import timedelta

from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.jobs import (
    claim_next_job,
    fail_unsupported_job,
    isoformat,
    recover_expired_jobs,
    utc_now,
)
from app.workers.worker import run_once


def test_claim_next_job_updates_status_and_lease(tmp_path):
    database_path = tmp_path / "db.sqlite3"
    now = utc_now()

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        conn.execute(
            "INSERT INTO jobs (job_type, status) VALUES ('preview', 'queued')"
        )
        job = claim_next_job(
            conn,
            lease_seconds=300,
            supported_job_types={"preview"},
            now=now,
        )

    assert job is not None
    assert job["status"] == "running"
    assert job["claimed_at"] == isoformat(now)
    assert job["lease_expires_at"] == isoformat(now + timedelta(seconds=300))


def test_recover_expired_jobs_moves_running_jobs_to_queued(tmp_path):
    database_path = tmp_path / "db.sqlite3"
    now = utc_now()
    expired_at = now - timedelta(seconds=1)

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        conn.execute(
            """
            INSERT INTO jobs (job_type, status, claimed_at, lease_expires_at)
            VALUES ('preview', 'running', ?, ?)
            """,
            (isoformat(now - timedelta(seconds=300)), isoformat(expired_at)),
        )
        recovered_count = recover_expired_jobs(conn, now=now)
        row = conn.execute("SELECT * FROM jobs").fetchone()

    assert recovered_count == 1
    assert row["status"] == "queued"
    assert row["claimed_at"] is None
    assert row["lease_expires_at"] is None


def test_claim_next_job_with_empty_supported_types_claims_nothing(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        conn.execute(
            "INSERT INTO jobs (job_type, status) VALUES ('preview', 'queued')"
        )
        job = claim_next_job(conn, lease_seconds=300, supported_job_types=set())
        row = conn.execute("SELECT * FROM jobs").fetchone()

    assert job is None
    assert row["status"] == "queued"


def test_claim_next_job_claims_only_supported_job_types(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        conn.execute(
            "INSERT INTO jobs (job_type, status) VALUES ('unsupported', 'queued')"
        )
        conn.execute(
            "INSERT INTO jobs (job_type, status) VALUES ('preview', 'queued')"
        )
        job = claim_next_job(
            conn,
            lease_seconds=300,
            supported_job_types={"preview"},
        )

    assert job is not None
    assert job["job_type"] == "preview"
    assert job["status"] == "running"


def test_worker_leaves_unsupported_job_queued(monkeypatch, tmp_path):
    media_root = tmp_path / "media"
    database_path = tmp_path / "db.sqlite3"
    monkeypatch.setenv("MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(database_path))

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        conn.execute(
            "INSERT INTO jobs (job_type, status) VALUES ('unsupported', 'queued')"
        )

    processed = run_once()

    with connect(database_path, 5000) as conn:
        row = conn.execute("SELECT * FROM jobs").fetchone()

    assert processed is False
    assert row["status"] == "queued"
    assert row["error_message"] is None


def test_explicit_unsupported_job_failure_helper_marks_claimed_job_failed(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        conn.execute(
            "INSERT INTO jobs (job_type, status) VALUES ('unsupported', 'queued')"
        )
        job = claim_next_job(
            conn,
            lease_seconds=300,
            supported_job_types={"unsupported"},
        )
        assert job is not None
        fail_unsupported_job(conn, job)
        row = conn.execute("SELECT * FROM jobs").fetchone()

    assert row["status"] == "failed"
    assert "Unsupported job type" in row["error_message"]
