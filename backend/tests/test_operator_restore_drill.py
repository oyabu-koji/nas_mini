import json
import os
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from app.core.settings import Settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.services.detector_v2_migration import (
    DetectorV2MigrationError,
    apply_detector_v2_migration,
)
from app.services.offline_startup_migration import (
    MIGRATION_CONTRACT,
    MIGRATIONS_DIR,
    OfflineStartupMigrationError,
    _apply_one,
    apply_offline_startup_migrations,
)
from app.services.operator_restore_drill import (
    OperatorRestoreDrillError,
    create_fresh_backup,
    inspect_database,
    restore_database,
    run_restore_drill,
    snapshot_media,
    write_disposable_marker,
)
from app.services.phase2b_migration import (
    Phase2BMigrationError,
    apply_phase2b_migration,
)
from app.services.phase2c_migration import (
    Phase2CMigrationError,
    apply_phase2c_migration,
)


@pytest.fixture
def disposable_root():
    nonce = f"test-{uuid4().hex}"
    root = Path("/private/tmp") / f"mediavault-operator-{nonce}"
    write_disposable_marker(root, nonce=nonce)
    try:
        yield root, nonce
    finally:
        if root.is_dir() and root.parent == Path("/private/tmp"):
            shutil.rmtree(root)


def _settings(root):
    built_in = root / "built-in"
    user_luts = root / "user-luts"
    media = root / "media"
    built_in.mkdir(exist_ok=True)
    user_luts.mkdir(exist_ok=True)
    media.mkdir(exist_ok=True)
    return Settings(
        media_root=media,
        api_token="test-token",
        database_path=root / "db.sqlite3",
        detector_root=root / "detector",
        built_in_preset_root=built_in,
        user_lut_root=user_luts,
    )


def _initialize_007(settings):
    with connect(settings.database_path, 5000) as conn:
        run_migrations(conn)


def _apply_008(settings, **kwargs):
    return apply_phase2b_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        certification_check=lambda _settings: None,
        **kwargs,
    )


def _apply_009(settings, **kwargs):
    return apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
        **kwargs,
    )


def _apply_010(settings, **kwargs):
    return apply_detector_v2_migration(
        settings=settings,
        mode="apply",
        offline_maintenance_confirmed=True,
        api_stopped_confirmed=True,
        release_040_ready_confirmed=True,
        isolated_database_confirmed=False,
        release_readiness_check=lambda _settings: True,
        **kwargs,
    )


def _backup(root, nonce, database):
    backup = root / "pre-release.sqlite3"
    identity = create_fresh_backup(
        source_database=database,
        backup_database=backup,
        disposable_root=root,
        nonce=nonce,
        database_volume="disposable-restore-test-db",
    )
    return backup, identity


def _restore(root, nonce, backup, database):
    return restore_database(
        backup_database=backup,
        target_database=database,
        disposable_root=root,
        nonce=nonce,
        database_volume="disposable-restore-test-db",
    )


def _prepare_media(media_root):
    for directory in ("originals", "previews", "thumbnails"):
        (media_root / directory).mkdir(parents=True, exist_ok=True)
    original = media_root / "originals/protected.bin"
    original.write_bytes(b"protected-original")
    os.utime(original, ns=(1_700_000_000_000_000_000,) * 2)
    return snapshot_media(media_root)


def _restore_with_media(root, nonce, backup, database, media_root, protected):
    orphan = media_root / "previews/operation-orphan.bin"
    orphan.write_bytes(b"operation-orphan")
    Path(f"{database}-wal").write_bytes(b"stale-wal")
    Path(f"{database}-shm").write_bytes(b"stale-shm")
    result = run_restore_drill(
        backup_database=backup,
        target_database=database,
        disposable_root=root,
        nonce=nonce,
        database_volume="disposable-restore-test-db",
        media_root=media_root,
        protected_before=protected,
        operation_derived_paths=("previews/operation-orphan.bin",),
    )
    assert result.removed_operation_orphans == 1
    assert snapshot_media(media_root) == protected
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()
    return inspect_database(database)


def test_restore_drill_restores_database_cleans_sidecars_and_only_operation_orphan(
    disposable_root,
):
    root, nonce = disposable_root
    settings = _settings(root)
    _initialize_007(settings)
    for directory in ("originals", "previews", "thumbnails"):
        (settings.media_root / directory).mkdir()
    original = settings.media_root / "originals/source.bin"
    existing = settings.media_root / "previews/existing.bin"
    original.write_bytes(b"original-do-not-change")
    existing.write_bytes(b"existing-derived")
    os.utime(original, ns=(1_700_000_000_000_000_000,) * 2)
    os.utime(existing, ns=(1_700_000_001_000_000_000,) * 2)
    with connect(settings.database_path, 5000) as conn:
        asset_id = conn.execute(
            """
            INSERT INTO assets (type, filename, original_path, size_bytes)
            VALUES ('video', 'source.bin', 'originals/source.bin', 22)
            """
        ).lastrowid
        conn.execute(
            """
            INSERT INTO derived_files (asset_id, kind, path, size_bytes)
            VALUES (?, 'preview', 'previews/existing.bin', ?)
            """,
            (asset_id, len(b"existing-derived")),
        )
        conn.commit()
    protected = snapshot_media(settings.media_root)
    backup, identity = _backup(root, nonce, settings.database_path)

    operation_orphan = settings.media_root / "previews/operation-orphan.bin"
    operation_orphan.write_bytes(b"orphan")
    with sqlite3.connect(settings.database_path) as conn:
        conn.execute("DELETE FROM derived_files")
        conn.commit()
    Path(f"{settings.database_path}-wal").write_bytes(b"stale-wal")
    Path(f"{settings.database_path}-shm").write_bytes(b"stale-shm")

    result = run_restore_drill(
        backup_database=backup,
        target_database=settings.database_path,
        disposable_root=root,
        nonce=nonce,
        database_volume="disposable-restore-test-db",
        media_root=settings.media_root,
        protected_before=protected,
        operation_derived_paths=("previews/operation-orphan.bin",),
    )

    assert result.status == "restore_drill_complete"
    assert result.removed_operation_orphans == 1
    assert inspect_database(settings.database_path) == identity
    assert snapshot_media(settings.media_root) == protected
    assert not operation_orphan.exists()
    forensic = root / "failure-forensic.json"
    assert os.stat(forensic).st_mode & 0o777 == 0o600
    forensic_payload = json.loads(forensic.read_text(encoding="utf-8"))
    assert forensic_payload["kind"] == "mediavault-operator-failure-forensic-v1"
    assert forensic_payload["nonce"] == nonce
    assert {item["name"] for item in forensic_payload["files"]} == {
        settings.database_path.name,
        f"{settings.database_path.name}-wal",
        f"{settings.database_path.name}-shm",
    }
    assert not Path(f"{settings.database_path}-wal").exists()
    assert not Path(f"{settings.database_path}-shm").exists()


def test_restore_rejects_operator_volume_before_mutation(disposable_root):
    root, nonce = disposable_root
    settings = _settings(root)
    _initialize_007(settings)

    with pytest.raises(
        OperatorRestoreDrillError, match="restore_database_volume_invalid"
    ):
        create_fresh_backup(
            source_database=settings.database_path,
            backup_database=root / "backup.sqlite3",
            disposable_root=root,
            nonce=nonce,
            database_volume="latest_template_backend-db",
        )

    assert not (root / "backup.sqlite3").exists()


def test_disposable_001_to_009_success_and_010_preflight_is_read_only(
    disposable_root,
):
    root, nonce = disposable_root
    settings = _settings(root)
    database = settings.database_path
    conn = sqlite3.connect(database)
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    first = MIGRATION_CONTRACT[0][0]
    sql = (MIGRATIONS_DIR / f"{first}.sql").read_text(encoding="utf-8")
    _apply_one(conn, version=first, sql=sql, fault_injector=None)
    conn.close()

    offline = apply_offline_startup_migrations(
        database_path=database,
        offline_maintenance_confirmed=True,
    )
    assert offline.last_committed_version == "007_managed_preview_presets"
    assert _apply_008(settings).status == "applied"
    assert _apply_009(settings, dry_run=True).status == "dry_run"
    assert _apply_009(settings).status == "applied"

    backup, expected = _backup(root, nonce, database)
    assert expected.versions[-1] == "009_safe_delete_candidate"
    assert _restore(root, nonce, backup, database) == expected
    before = database.read_bytes()
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()

    preflight = apply_detector_v2_migration(settings=settings)

    assert preflight.status == "preflight_ready"
    assert database.read_bytes() == before
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()


@pytest.mark.parametrize(
    "target",
    [version for version, _digest in MIGRATION_CONTRACT[1:]],
)
@pytest.mark.parametrize("fault_position", ["mid", "commit"])
def test_002_007_fault_state_restores_database_sidecars_and_media(
    disposable_root, target, fault_position
):
    root, nonce = disposable_root
    database = root / "db.sqlite3"
    conn = sqlite3.connect(database)
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    version = MIGRATION_CONTRACT[0][0]
    sql = (MIGRATIONS_DIR / f"{version}.sql").read_text(encoding="utf-8")
    _apply_one(conn, version=version, sql=sql, fault_injector=None)
    conn.close()
    media_root = root / "media"
    media_root.mkdir()
    protected = _prepare_media(media_root)
    backup, expected = _backup(root, nonce, database)

    def inject(step):
        expected_step = (
            f"after_{target}_statement_1"
            if fault_position == "mid"
            else f"after_{target}_commit"
        )
        if step == expected_step:
            raise RuntimeError("fault")

    with pytest.raises(OfflineStartupMigrationError):
        apply_offline_startup_migrations(
            database_path=database,
            offline_maintenance_confirmed=True,
            fault_injector=inject,
        )

    assert (
        _restore_with_media(root, nonce, backup, database, media_root, protected)
        == expected
    )


@pytest.mark.parametrize("fault_position", ["pre", "mid", "post"])
@pytest.mark.parametrize("phase", ["008", "009", "010"])
def test_phase_fault_matrix_restores_exact_pre_phase_backup(
    disposable_root, phase, fault_position
):
    root, nonce = disposable_root
    settings = _settings(root)
    _initialize_007(settings)
    apply = _apply_008
    fault_step = (
        "after_read_preflight" if fault_position == "pre" else "after_statement_1"
    )
    if phase in {"009", "010"}:
        _apply_008(settings)
        apply = _apply_009
    if phase == "010":
        _apply_009(settings)
        apply = _apply_010
        if fault_position == "mid":
            fault_step = "after_trigger_drop"
    protected = _prepare_media(settings.media_root)
    backup, expected = _backup(root, nonce, settings.database_path)

    if fault_position == "post":

        def post_commit_fault(step):
            if step == "after_commit":
                raise RuntimeError("fault")

        with pytest.raises(
            (Phase2BMigrationError, Phase2CMigrationError, DetectorV2MigrationError)
        ) as captured:
            apply(settings, fault_injector=post_commit_fault)
        assert captured.value.restore_required is True
    else:

        def inject(step):
            if step == fault_step:
                raise RuntimeError("fault")

        with pytest.raises(RuntimeError, match="fault"):
            apply(settings, fault_injector=inject)

    assert (
        _restore_with_media(
            root,
            nonce,
            backup,
            settings.database_path,
            settings.media_root,
            protected,
        )
        == expected
    )


def test_media_reconciliation_refuses_changed_original(disposable_root):
    root, nonce = disposable_root
    settings = _settings(root)
    _initialize_007(settings)
    (settings.media_root / "originals").mkdir()
    original = settings.media_root / "originals/source.bin"
    original.write_bytes(b"before")
    protected = snapshot_media(settings.media_root)
    backup, _identity = _backup(root, nonce, settings.database_path)
    original.write_bytes(b"after")

    with pytest.raises(
        OperatorRestoreDrillError, match="restore_media_protected_changed"
    ):
        run_restore_drill(
            backup_database=backup,
            target_database=settings.database_path,
            disposable_root=root,
            nonce=nonce,
            database_volume="disposable-restore-test-db",
            media_root=settings.media_root,
            protected_before=protected,
            operation_derived_paths=(),
        )

    assert original.read_bytes() == b"after"


def test_restore_refuses_media_root_outside_disposable_root(disposable_root):
    root, nonce = disposable_root
    settings = _settings(root)
    _initialize_007(settings)
    backup, _identity = _backup(root, nonce, settings.database_path)
    external_media = Path("/private/tmp") / f"external-media-{uuid4().hex}"
    external_media.mkdir(mode=0o700)
    protected = external_media / "originals" / "protected.bin"
    protected.parent.mkdir()
    protected.write_bytes(b"must-remain")
    try:
        with pytest.raises(
            OperatorRestoreDrillError, match="restore_media_root_invalid"
        ):
            run_restore_drill(
                backup_database=backup,
                target_database=settings.database_path,
                disposable_root=root,
                nonce=nonce,
                database_volume="disposable-restore-test-db",
                media_root=external_media,
                protected_before=snapshot_media(external_media),
                operation_derived_paths=(),
            )
        assert protected.read_bytes() == b"must-remain"
    finally:
        shutil.rmtree(external_media)
