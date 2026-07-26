import hashlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.db.phase2b import PHASE2B_MIGRATION_VERSION, schema_sql_sha256
from app.services.phase2b_migration import (
    Phase2BMigrationError,
    apply_phase2b_migration,
)
from app.services.processed_result_delivery import resolve_deliverable_result
from tests.test_phase2b_schema import _insert_session_asset
from tests.test_phase2b_schema import _insert_ready_managed


def _settings(tmp_path):
    return Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
        detector_root=tmp_path / "detector",
    )


def _initialize(settings):
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)


def _certified(_settings):
    return None


def test_phase2b_migration_applies_schema_identity_marker_and_is_repeatable(tmp_path):
    settings = _settings(tmp_path)
    _initialize(settings)

    applied = apply_phase2b_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        certification_check=_certified,
    )
    repeated = apply_phase2b_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        certification_check=_certified,
    )

    assert applied.status == "applied"
    assert repeated.status == "already_applied"
    with connect(settings.database_path, 5000) as conn:
        marker_count = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (PHASE2B_MIGRATION_VERSION,),
        ).fetchone()[0]
        identity = conn.execute(
            "SELECT schema_sql_sha256 FROM phase2b_schema_metadata"
        ).fetchone()[0]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert marker_count == 1
    assert identity == schema_sql_sha256()


def test_phase2b_migration_rolls_back_schema_and_marker_on_write_failure(tmp_path):
    settings = _settings(tmp_path)
    _initialize(settings)

    def fail(step):
        if step == "after_statement_3":
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        apply_phase2b_migration(
            settings=settings,
            offline_maintenance_confirmed=True,
            certification_check=_certified,
            fault_injector=fail,
        )

    with connect(settings.database_path, 5000) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(assets)")}
        marker = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (PHASE2B_MIGRATION_VERSION,),
        ).fetchone()
    assert "preview_generation" not in columns
    assert marker is None


def test_phase2b_migration_rejects_pending_work_without_schema_change(tmp_path):
    settings = _settings(tmp_path)
    _initialize(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            """
            INSERT INTO jobs (job_type, status, payload_json, dedup_key)
            VALUES ('preview', 'queued', '{}', 'pending-preview')
            """
        )
        conn.commit()

    with pytest.raises(
        Phase2BMigrationError, match="phase2b_migration_preview_not_drained"
    ):
        apply_phase2b_migration(
            settings=settings,
            offline_maintenance_confirmed=True,
            certification_check=_certified,
        )

    with connect(settings.database_path, 5000) as conn:
        assert "preview_generation" not in {
            row["name"] for row in conn.execute("PRAGMA table_info(assets)")
        }


@pytest.mark.parametrize("writer_kind", ["job", "rendition", "asset"])
def test_phase2b_migration_rechecks_writers_after_read_preflight(
    tmp_path, writer_kind
):
    settings = _settings(tmp_path)
    _initialize(settings)
    with connect(settings.database_path, 5000) as conn:
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        conn.execute(
            "UPDATE assets SET preview_status = 'preview_ready' WHERE id = 1"
        )
        conn.commit()

    def write_after_preflight(step):
        if step != "after_read_preflight":
            return
        with connect(settings.database_path, 5000) as writer:
            if writer_kind == "job":
                writer.execute(
                    """
                    INSERT INTO jobs (
                        job_type, status, asset_id, payload_json, dedup_key
                    ) VALUES ('preview', 'queued', 1, '{}', 'competing-preview')
                    """
                )
            elif writer_kind == "rendition":
                job_id = writer.execute(
                    """
                    INSERT INTO jobs (
                        job_type, status, asset_id, payload_json, dedup_key
                    ) VALUES ('rendition', 'done', 1, '{}', 'competing-rendition')
                    """
                ).lastrowid
                writer.execute(
                    """
                    INSERT INTO renditions (
                        id, asset_id, client_request_id, job_id,
                        selection_generation, requested_preset_id,
                        registry_classification, state
                    ) VALUES (?, 1, ?, ?, 1, 'compress-only', 'valid', 'queued')
                    """,
                    ("a" * 32, "b" * 32, job_id),
                )
            else:
                writer.execute(
                    """
                    UPDATE assets
                    SET preview_status = 'preview_generating'
                    WHERE id = 1
                    """
                )
            writer.commit()

    with pytest.raises(
        Phase2BMigrationError, match="phase2b_migration_preview_not_drained"
    ):
        apply_phase2b_migration(
            settings=settings,
            offline_maintenance_confirmed=True,
            certification_check=_certified,
            fault_injector=write_after_preflight,
        )

    with connect(settings.database_path, 5000) as conn:
        assert "preview_generation" not in {
            row["name"] for row in conn.execute("PRAGMA table_info(assets)")
        }
        marker = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (PHASE2B_MIGRATION_VERSION,),
        ).fetchone()
        pending_jobs = conn.execute(
            """
            SELECT COUNT(*) FROM jobs
            WHERE job_type IN ('preview', 'lut_preview', 'rendition')
              AND status IN ('queued', 'running')
            """
        ).fetchone()[0]
        pending_renditions = conn.execute(
            """
            SELECT COUNT(*) FROM renditions
            WHERE state IN ('queued', 'validating', 'rendering', 'finalizing')
            """
        ).fetchone()[0]
        generating_assets = conn.execute(
            "SELECT COUNT(*) FROM assets WHERE preview_status = 'preview_generating'"
        ).fetchone()[0]
    assert marker is None
    assert {
        "job": (1, 0, 0),
        "rendition": (0, 1, 0),
        "asset": (0, 0, 1),
    }[writer_kind] == (pending_jobs, pending_renditions, generating_assets)


def test_phase2b_migration_rejects_schema_identity_mismatch(tmp_path):
    settings = _settings(tmp_path)
    _initialize(settings)
    apply_phase2b_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        certification_check=_certified,
    )
    with connect(settings.database_path, 5000) as conn:
        conn.execute("DROP TRIGGER prevent_preview_provenance_update")
        conn.execute(
            "UPDATE phase2b_schema_metadata SET schema_sql_sha256 = ?",
            ("0" * 64,),
        )
        conn.commit()

    with pytest.raises(
        Phase2BMigrationError, match="phase2b_migration_schema_identity_mismatch"
    ):
        apply_phase2b_migration(
            settings=settings,
            offline_maintenance_confirmed=True,
            certification_check=_certified,
        )


@pytest.mark.parametrize("preview_status", ["preview_ready", "failed"])
def test_phase2b_migration_enqueues_generation_one_for_eligible_assets(
    tmp_path, preview_status
):
    settings = _settings(tmp_path)
    _initialize(settings)
    with connect(settings.database_path, 5000) as conn:
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        conn.execute(
            "UPDATE assets SET preview_status = ? WHERE id = 1",
            (preview_status,),
        )
        conn.commit()

    apply_phase2b_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        certification_check=_certified,
    )

    with connect(settings.database_path, 5000) as conn:
        asset = conn.execute("SELECT * FROM assets WHERE id = 1").fetchone()
        job = conn.execute(
            "SELECT * FROM jobs WHERE dedup_key = 'phase2b-profile-preview:1'"
        ).fetchone()
    assert asset["preview_generation"] == 1
    assert asset["preview_status"] == "preview_generating"
    assert asset["review_status"] == "not_reviewed"
    assert job["job_type"] == "preview"
    assert job["preview_generation"] == 1


def test_phase2b_migration_does_not_change_direct_or_image_assets(tmp_path):
    settings = _settings(tmp_path)
    _initialize(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            """
            INSERT INTO assets (
                id, type, filename, transfer_status, verification_status,
                preview_status
            ) VALUES (1, 'video', 'direct.mov', 'transferred',
                      'file_verified', 'preview_ready')
            """
        )
        conn.execute(
            """
            INSERT INTO assets (
                id, type, filename, transfer_status, verification_status,
                preview_status
            ) VALUES (2, 'image', 'photo.jpg', 'transferred',
                      'file_verified', 'preview_ready')
            """
        )
        conn.commit()

    apply_phase2b_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        certification_check=_certified,
    )

    with connect(settings.database_path, 5000) as conn:
        rows = conn.execute(
            "SELECT id, preview_generation, preview_status FROM assets ORDER BY id"
        ).fetchall()
        jobs = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE dedup_key LIKE 'phase2b-profile-preview:%'"
        ).fetchone()[0]
    assert [(row["id"], row["preview_generation"], row["preview_status"]) for row in rows] == [
        (1, 0, "preview_ready"),
        (2, 0, "preview_ready"),
    ]
    assert jobs == 0


def test_existing_phase2b_dedup_job_leaves_asset_unchanged(tmp_path):
    settings = _settings(tmp_path)
    _initialize(settings)
    with connect(settings.database_path, 5000) as conn:
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        conn.execute(
            "UPDATE assets SET preview_status = 'failed' WHERE id = 1"
        )
        conn.execute(
            """
            INSERT INTO jobs (
                job_type, status, asset_id, payload_json, dedup_key
            ) VALUES ('preview', 'done', 1, '{}', 'phase2b-profile-preview:1')
            """
        )
        conn.commit()

    apply_phase2b_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        certification_check=_certified,
    )

    with connect(settings.database_path, 5000) as conn:
        asset = conn.execute("SELECT * FROM assets WHERE id = 1").fetchone()
        jobs = conn.execute(
            """
            SELECT COUNT(*) FROM jobs
            WHERE dedup_key = 'phase2b-profile-preview:1'
            """
        ).fetchone()[0]
    assert asset["preview_generation"] == 0
    assert asset["preview_status"] == "failed"
    assert jobs == 1


def test_migration_supersedes_legacy_preview_after_clearing_pointer(tmp_path):
    settings = _settings(tmp_path)
    _initialize(settings)
    with connect(settings.database_path, 5000) as conn:
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        derived_id = conn.execute(
            """
            INSERT INTO derived_files (asset_id, kind, path, mime_type, size_bytes)
            VALUES (1, 'preview', 'previews/legacy.mp4', 'video/mp4', 10)
            """
        ).lastrowid
        result_id = "1" * 32
        conn.execute(
            """
            INSERT INTO processed_results (
                id, asset_id, derived_file_id, status, mime_type, size_bytes,
                sha256, preview_generation
            ) VALUES (?, 1, ?, 'ready', 'video/mp4', 10, ?, NULL)
            """,
            (result_id, derived_id, "a" * 64),
        )
        conn.execute(
            """
            UPDATE assets
            SET active_processed_result_id = ?, preview_status = 'preview_ready'
            WHERE id = 1
            """,
            (result_id,),
        )
        conn.commit()

    apply_phase2b_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        certification_check=_certified,
    )

    with connect(settings.database_path, 5000) as conn:
        asset = conn.execute("SELECT * FROM assets WHERE id = 1").fetchone()
        result = conn.execute(
            "SELECT * FROM processed_results WHERE id = ?", (result_id,)
        ).fetchone()
    assert asset["active_processed_result_id"] is None
    assert asset["preview_generation"] == 1
    assert result["status"] == "superseded"


def test_migration_preserves_current_managed_authority(tmp_path):
    settings = _settings(tmp_path)
    _initialize(settings)
    managed_bytes = b"managed-result"
    managed_sha256 = hashlib.sha256(managed_bytes).hexdigest()
    with connect(settings.database_path, 5000) as conn:
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        managed_id = _insert_ready_managed(
            conn,
            generation=1,
            result_id="2" * 32,
            rendition_id="3" * 32,
            size_bytes=len(managed_bytes),
            sha256=managed_sha256,
        )
        conn.execute(
            """
            UPDATE assets
            SET active_processed_result_id = ?, preview_status = 'preview_ready'
            WHERE id = 1
            """,
            (managed_id,),
        )
        conn.commit()
    managed_path = settings.media_root / f"previews/renditions/{'3' * 32}.mp4"
    managed_path.parent.mkdir(parents=True)
    managed_path.write_bytes(managed_bytes)

    apply_phase2b_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        certification_check=_certified,
    )

    with connect(settings.database_path, 5000) as conn:
        asset = conn.execute("SELECT * FROM assets WHERE id = 1").fetchone()
        result = conn.execute(
            "SELECT * FROM processed_results WHERE id = ?", (managed_id,)
        ).fetchone()
        provenance_count = conn.execute(
            "SELECT COUNT(*) FROM rendition_provenance WHERE result_id = ?",
            (managed_id,),
        ).fetchone()[0]
        deliverable = resolve_deliverable_result(
            settings=settings,
            conn=conn,
            asset=dict(asset),
        )
    assert asset["active_processed_result_id"] == managed_id
    assert asset["preview_generation"] == 1
    assert asset["preview_status"] == "preview_generating"
    assert result["status"] == "ready"
    assert result["preview_generation"] is None
    assert provenance_count == 1
    assert deliverable is not None
    assert deliverable.result["id"] == managed_id


def test_ambiguous_managed_authority_rolls_back_entire_migration(tmp_path):
    settings = _settings(tmp_path)
    _initialize(settings)
    with connect(settings.database_path, 5000) as conn:
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        old_id = _insert_ready_managed(
            conn,
            generation=1,
            result_id="2" * 32,
            rendition_id="3" * 32,
        )
        _insert_ready_managed(
            conn,
            generation=2,
            result_id="4" * 32,
            rendition_id="5" * 32,
        )
        conn.execute(
            """
            UPDATE assets
            SET active_processed_result_id = ?, preview_status = 'preview_ready'
            WHERE id = 1
            """,
            (old_id,),
        )
        conn.commit()

    with pytest.raises(
        Phase2BMigrationError, match="phase2b_migration_active_result_ambiguous"
    ):
        apply_phase2b_migration(
            settings=settings,
            offline_maintenance_confirmed=True,
            certification_check=_certified,
        )

    with connect(settings.database_path, 5000) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(assets)")}
        marker = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (PHASE2B_MIGRATION_VERSION,),
        ).fetchone()
        active_id = conn.execute(
            "SELECT active_processed_result_id FROM assets WHERE id = 1"
        ).fetchone()[0]
    assert "preview_generation" not in columns
    assert marker is None
    assert active_id == old_id


def test_concurrent_phase2b_apply_creates_one_generation_job(tmp_path):
    settings = _settings(tmp_path)
    _initialize(settings)
    with connect(settings.database_path, 5000) as conn:
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        conn.execute("UPDATE assets SET preview_status = 'failed' WHERE id = 1")
        conn.commit()
    barrier = threading.Barrier(2)

    def concurrent_certification(_settings):
        barrier.wait(timeout=5)

    def migrate():
        return apply_phase2b_migration(
            settings=settings,
            offline_maintenance_confirmed=True,
            certification_check=concurrent_certification,
        ).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _index: migrate(), range(2)))

    assert statuses == ["already_applied", "applied"]
    with connect(settings.database_path, 5000) as conn:
        jobs = conn.execute(
            """
            SELECT COUNT(*) FROM jobs
            WHERE dedup_key = 'phase2b-profile-preview:1'
            """
        ).fetchone()[0]
        generation = conn.execute(
            "SELECT preview_generation FROM assets WHERE id = 1"
        ).fetchone()[0]
    assert jobs == 1
    assert generation == 1
