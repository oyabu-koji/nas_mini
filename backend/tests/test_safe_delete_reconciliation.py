import pytest

from app.core.settings import Settings
from app.db.connection import connect
from app.services.detector_capability import DetectorCapability
from app.services.phase2c_migration import apply_phase2c_migration
from app.services.safe_delete_reconciliation import (
    reconcile_safe_delete_candidates,
)
from tests.phase2c_test_support import (
    initialize_phase2b,
    insert_eligible_confirmed_asset,
)


def _settings(tmp_path):
    return Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
        detector_root=tmp_path / "detector",
    )


def _runtime(available):
    return DetectorCapability(
        mode="phase2b_enabled" if available else "phase2a_compatibility",
        detector_certified=available,
        formal_apple_log_preview=available,
        blocked_reason=None if available else "log_detector_manifest_invalid",
    )


def test_reconciliation_dry_run_and_apply_share_candidate_evaluator(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn, review_status="not_reviewed")
        conn.commit()
    apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "UPDATE assets SET review_status = 'preview_confirmed' WHERE id = 1"
        )
        conn.commit()
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(True),
    )

    dry_run = reconcile_safe_delete_candidates(
        settings=settings,
        apply_changes=False,
    )
    with connect(settings.database_path, 5000) as conn:
        after_dry_run = conn.execute(
            "SELECT delete_candidate_status FROM assets WHERE id = 1"
        ).fetchone()[0]
    applied = reconcile_safe_delete_candidates(
        settings=settings,
        apply_changes=True,
    )

    assert dry_run.status == "dry_run"
    assert dry_run.promoted == 1
    assert after_dry_run == "not_candidate"
    assert applied.status == "applied"
    assert applied.promoted == 1


def test_reconciliation_runtime_unavailable_does_not_promote(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn, review_status="not_reviewed")
        conn.commit()
    apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "UPDATE assets SET review_status = 'preview_confirmed' WHERE id = 1"
        )
        conn.commit()
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(False),
    )

    result = reconcile_safe_delete_candidates(
        settings=settings,
        apply_changes=True,
    )

    assert result.promoted == 0
    assert result.unchanged == 1
    assert result.examined == 1


def _invalidate_formal_relation(settings):
    with connect(settings.database_path, 5000) as conn:
        trigger_sql = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'trigger'
              AND name = 'prevent_preview_provenance_update'
            """
        ).fetchone()[0]
        conn.execute("DROP TRIGGER prevent_preview_provenance_update")
        conn.execute(
            """
            UPDATE preview_provenance
            SET source_profile = 'mismatch'
            WHERE asset_id = 1
            """
        )
        conn.execute(trigger_sql)
        conn.commit()


def test_reconciliation_demotes_invalid_safe_and_preserves_other_authority(
    tmp_path,
    monkeypatch,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        conn.commit()
    apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )
    _invalidate_formal_relation(settings)
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(True),
    )
    with connect(settings.database_path, 5000) as conn:
        authority_before = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in (
                "upload_sessions",
                "upload_chunks",
                "processed_results",
                "derived_files",
                "formal_preview_attempts",
                "preview_provenance",
            )
        }

    result = reconcile_safe_delete_candidates(
        settings=settings,
        apply_changes=True,
    )

    with connect(settings.database_path, 5000) as conn:
        candidate = conn.execute(
            "SELECT delete_candidate_status FROM assets WHERE id = 1"
        ).fetchone()[0]
        authority_after = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in authority_before
        }
    assert result.examined == 1
    assert result.demoted == 1
    assert result.reasons == {"formal_preview_provenance_invalid": 1}
    assert candidate == "not_candidate"
    assert authority_after == authority_before


def test_reconciliation_runtime_unavailable_still_demotes_invalid_safe(
    tmp_path,
    monkeypatch,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        conn.commit()
    apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )
    _invalidate_formal_relation(settings)
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(False),
    )

    result = reconcile_safe_delete_candidates(
        settings=settings,
        apply_changes=True,
    )

    assert result.promoted == 0
    assert result.demoted == 1


def test_reconciliation_keeps_valid_safe_candidate_unchanged(
    tmp_path,
    monkeypatch,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        conn.commit()
    apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(True),
    )

    result = reconcile_safe_delete_candidates(
        settings=settings,
        apply_changes=True,
    )

    assert result.examined == 1
    assert result.promoted == 0
    assert result.demoted == 0
    assert result.unchanged == 1
    assert result.reasons == {}


def test_reconciliation_rechecks_schema_identity_inside_write_lock(
    tmp_path,
    monkeypatch,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )
    from app.services import safe_delete_reconciliation as service

    real_resolver = service.resolve_phase2_rollout

    def tamper_after_runtime(**kwargs):
        snapshot = real_resolver(**kwargs)
        with connect(settings.database_path, 5000) as conn:
            conn.execute(
                "DROP TRIGGER prevent_completed_upload_chunk_insert"
            )
            conn.commit()
        return snapshot

    monkeypatch.setattr(service, "resolve_phase2_rollout", tamper_after_runtime)

    with pytest.raises(
        RuntimeError,
        match="phase2c_migration_schema_identity_mismatch",
    ):
        reconcile_safe_delete_candidates(
            settings=settings,
            apply_changes=True,
        )
