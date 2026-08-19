import json
import os
from types import SimpleNamespace

import pytest
from app.services.disposable_database_target import (
    MARKER_NAME,
    DisposableDatabaseTargetError,
    claim_disposable_database_operation,
    initialize_disposable_database_target,
    require_disposable_database_target,
)

from scripts import migrate_phase2b_operator_safe as phase2b_cli
from scripts import migrate_phase2c_operator_safe as phase2c_cli
from scripts import migrate_startup_offline as startup_cli
from scripts import preflight_detector_v2 as preflight_cli
from scripts import run_operator_worker_drain as worker_cli

VOLUME = "disposable-test-volume"
NONCE = "test-disposable-0001"


def test_initializer_creates_owner_only_exact_marker_and_is_idempotent(tmp_path):
    database = tmp_path / "mediavault.sqlite3"

    initialize_disposable_database_target(
        database_path=database, volume_name=VOLUME, nonce=NONCE
    )
    initialize_disposable_database_target(
        database_path=database, volume_name=VOLUME, nonce=NONCE
    )

    marker = tmp_path / MARKER_NAME
    assert os.stat(marker).st_mode & 0o777 == 0o600
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "kind": "mediavault-operator-disposable-volume-v1",
        "nonce": NONCE,
        "volume": VOLUME,
    }
    require_disposable_database_target(
        database_path=database, volume_name=VOLUME, nonce=NONCE
    )


@pytest.mark.parametrize(
    ("volume", "nonce"),
    [
        ("latest_template_backend-db", NONCE),
        ("not-disposable", NONCE),
        (VOLUME, "short"),
    ],
)
def test_initializer_rejects_unsafe_identity_without_marker(tmp_path, volume, nonce):
    with pytest.raises(DisposableDatabaseTargetError):
        initialize_disposable_database_target(
            database_path=tmp_path / "db.sqlite3",
            volume_name=volume,
            nonce=nonce,
        )
    assert not (tmp_path / MARKER_NAME).exists()


def test_guard_rejects_mismatched_or_permissive_marker(tmp_path):
    database = tmp_path / "db.sqlite3"
    initialize_disposable_database_target(
        database_path=database, volume_name=VOLUME, nonce=NONCE
    )
    marker = tmp_path / MARKER_NAME
    marker.chmod(0o640)

    with pytest.raises(DisposableDatabaseTargetError):
        require_disposable_database_target(
            database_path=database, volume_name=VOLUME, nonce=NONCE
        )


def test_guard_rejects_symlink_marker(tmp_path):
    database = tmp_path / "db.sqlite3"
    target = tmp_path / "marker-target"
    target.write_text("{}", encoding="utf-8")
    (tmp_path / MARKER_NAME).symlink_to(target)

    with pytest.raises(DisposableDatabaseTargetError):
        require_disposable_database_target(
            database_path=database, volume_name=VOLUME, nonce=NONCE
        )


def test_operation_claim_is_owner_only_and_rejects_automatic_resume(tmp_path):
    database = tmp_path / "db.sqlite3"
    initialize_disposable_database_target(
        database_path=database, volume_name=VOLUME, nonce=NONCE
    )
    claim_disposable_database_operation(
        database_path=database,
        nonce=NONCE,
        operation="offline-002-007",
    )
    claim = tmp_path / ".mediavault-offline-002-007.claim.json"
    assert os.stat(claim).st_mode & 0o777 == 0o600

    with pytest.raises(
        DisposableDatabaseTargetError,
        match="operator_disposable_operation_already_claimed",
    ):
        claim_disposable_database_operation(
            database_path=database,
            nonce=NONCE,
            operation="offline-002-007",
        )


@pytest.mark.parametrize("entrypoint", ["startup", "phase2b", "phase2c", "worker"])
def test_mutating_entrypoints_reject_operator_volume_before_delegate(
    monkeypatch, tmp_path, entrypoint
):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("OPERATOR_DATABASE_VOLUME_NAME", "latest_template_backend-db")
    monkeypatch.setenv("OPERATOR_DISPOSABLE_NONCE", NONCE)
    called = []
    if entrypoint == "startup":
        monkeypatch.setattr(
            startup_cli,
            "apply_offline_startup_migrations",
            lambda **_kwargs: called.append(True),
        )
        result = startup_cli.main(["--apply", "--offline-maintenance-confirmed"])
    elif entrypoint == "phase2b":
        monkeypatch.setattr(
            phase2b_cli, "migration_main", lambda _argv: called.append(True)
        )
        result = phase2b_cli.main()
    elif entrypoint == "phase2c":
        monkeypatch.setattr(
            phase2c_cli, "migration_main", lambda _argv: called.append(True)
        )
        result = phase2c_cli.main(["--apply", "--offline-maintenance-confirmed"])
    else:
        result = worker_cli.main()
    assert result == 1
    assert called == []


def test_read_only_preflight_rejects_operator_volume_before_preflight(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("OPERATOR_DATABASE_VOLUME_NAME", "latest_template_backend-db")
    monkeypatch.setenv("OPERATOR_DISPOSABLE_NONCE", NONCE)
    monkeypatch.setattr(
        preflight_cli,
        "load_settings",
        lambda: SimpleNamespace(database_path=tmp_path / "db.sqlite3"),
    )
    called = []
    monkeypatch.setattr(
        preflight_cli,
        "apply_detector_v2_migration",
        lambda **_kwargs: called.append(True),
    )

    assert preflight_cli.main([]) == 1
    assert called == []
