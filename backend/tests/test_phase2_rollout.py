import pytest

from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.db.phase_schema_identity import PhaseSchemaIdentityError
from app.services.client_compatibility import IncompatibleClientError
from app.services.detector_capability import DetectorCapability
from app.services.phase2_rollout import resolve_phase2_rollout
from app.services.phase2c_migration import apply_phase2c_migration
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


def _runtime(*, available):
    return DetectorCapability(
        mode="phase2b_enabled" if available else "phase2a_compatibility",
        detector_certified=available,
        formal_apple_log_preview=available,
        blocked_reason=None if available else "log_detector_manifest_invalid",
    )


def test_valid_phase2c_keeps_030_floor_when_runtime_is_unavailable(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(available=False),
    )

    snapshot = resolve_phase2_rollout(settings=settings)

    assert snapshot.minimum_client_version == "0.3.0"
    assert snapshot.phase2c_schema_enabled is True
    assert snapshot.formal_apple_log_preview is False
    assert snapshot.safe_delete_candidate is False


def test_client_error_precedes_runtime_probe_for_valid_phase2_asset(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            """
            INSERT INTO assets (
                id, type, filename, transfer_status, verification_status,
                preview_status
            ) VALUES (1, 'video', 'clip.mov', 'transferred',
                      'file_verified', 'preview_generating')
            """
        )
        conn.execute(
            """
            INSERT INTO upload_sessions (
                id, client_upload_id, type, filename, size_bytes,
                expected_file_sha256, chunk_size_bytes,
                original_relative_path, status, retryable, attempt_count,
                last_activity_at, expires_at, asset_id
            ) VALUES (
                'session-one', 'client-session-one', 'video', 'clip.mov', 8,
                ?, 8, 'originals/clip.mov', 'completed', 0, 0,
                CURRENT_TIMESTAMP, datetime('now', '+1 day'), 1
            )
            """,
            ("a" * 64,),
        )
        conn.commit()
    probes = []
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: probes.append(True),
    )

    with pytest.raises(IncompatibleClientError):
        resolve_phase2_rollout(
            settings=settings,
            asset_id=1,
            client_version="0.1.0",
            require_client_for_phase2_asset=True,
        )

    assert probes == []


def test_schema_identity_error_precedes_client_and_runtime(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute("DROP TRIGGER validate_formal_preview_ready")
        conn.commit()
    probes = []
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: probes.append(True),
    )

    with pytest.raises(
        PhaseSchemaIdentityError,
        match="phase2b_migration_schema_identity_mismatch",
    ):
        resolve_phase2_rollout(
            settings=settings,
            asset_id=1,
            client_version="0.1.0",
            require_client_for_phase2_asset=True,
        )

    assert probes == []


@pytest.mark.parametrize(
    ("phase", "runtime_available", "minimum", "formal", "safe"),
    [
        ("pre-008", False, None, False, False),
        ("008", False, "0.2.0", False, False),
        ("008", True, "0.2.0", True, False),
        ("009", False, "0.3.0", False, False),
        ("009", True, "0.3.0", True, True),
    ],
)
def test_rollout_schema_runtime_matrix(
    tmp_path,
    monkeypatch,
    phase,
    runtime_available,
    minimum,
    formal,
    safe,
):
    settings = _settings(tmp_path)
    if phase == "pre-008":
        with connect(settings.database_path, 5000) as conn:
            run_migrations(conn)
    else:
        initialize_phase2b(settings)
        if phase == "009":
            apply_phase2c_migration(
                settings=settings,
                offline_maintenance_confirmed=True,
                runtime_check=lambda _settings: True,
            )
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(available=runtime_available),
    )

    snapshot = resolve_phase2_rollout(settings=settings)

    assert snapshot.minimum_client_version == minimum
    assert snapshot.formal_apple_log_preview is formal
    assert snapshot.safe_delete_candidate is safe


@pytest.mark.parametrize("client_version", [None, "", "v0.3.0", "0.2.0"])
def test_phase2c_rejects_missing_malformed_or_old_client(
    tmp_path,
    monkeypatch,
    client_version,
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
        lambda _settings: _runtime(available=True),
    )

    with pytest.raises(IncompatibleClientError):
        resolve_phase2_rollout(
            settings=settings,
            asset_id=1,
            client_version=client_version,
            require_client_for_phase2_asset=True,
        )


def test_phase2c_accepts_client_030_for_phase2_asset(tmp_path, monkeypatch):
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
        lambda _settings: _runtime(available=True),
    )

    snapshot = resolve_phase2_rollout(
        settings=settings,
        asset_id=1,
        client_version="0.3.0",
        require_client_for_phase2_asset=True,
    )

    assert snapshot.phase2_asset is True
    assert snapshot.safe_delete_candidate is True


def test_phase1_asset_does_not_require_phase2_client_header(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            """
            INSERT INTO assets (
                id, type, filename, transfer_status,
                verification_status, preview_status
            ) VALUES (
                1, 'image', 'fixture.jpg', 'transferred',
                'server_hash_recorded', 'preview_ready'
            )
            """
        )
        conn.commit()
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: _runtime(available=True),
    )

    snapshot = resolve_phase2_rollout(
        settings=settings,
        asset_id=1,
        client_version=None,
        require_client_for_phase2_asset=True,
    )

    assert snapshot.phase2_asset is False


def test_invalid_phase2c_identity_precedes_old_client_and_runtime(
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
    with connect(settings.database_path, 5000) as conn:
        conn.execute("DROP TRIGGER prevent_completed_upload_chunk_insert")
        conn.commit()
    probes = []
    monkeypatch.setattr(
        "app.services.phase2_rollout.evaluate_detector_runtime",
        lambda _settings: probes.append(True),
    )

    with pytest.raises(
        PhaseSchemaIdentityError,
        match="^phase2c_migration_schema_identity_mismatch$",
    ):
        resolve_phase2_rollout(
            settings=settings,
            asset_id=1,
            client_version="0.2.0",
            require_client_for_phase2_asset=True,
        )

    assert probes == []
