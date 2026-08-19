import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest
from app.core.settings import Settings
from app.db.connection import connect
from app.db.detector_v2 import DETECTOR_V2_MIGRATION_VERSION
from app.db.detector_v2.schema import (
    DETECTOR_V2_REFERENCING_TRIGGER_NAMES,
    DETECTOR_V2_TRIGGER_SQL_SHA256,
)
from app.db.phase_schema_identity import resolve_managed_phase_schema
from app.services.detector_v2_migration import (
    DetectorV2MigrationError,
    _restore_default_pragmas,
    apply_detector_v2_migration,
)
from app.services.phase2c_migration import apply_phase2c_migration
from tests.phase2c_test_support import initialize_phase2b
from tests.test_preset_registry import write_custom


def _settings(tmp_path):
    built_in = tmp_path / "built-in"
    user = tmp_path / "user"
    built_in.mkdir()
    user.mkdir()
    return Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
        detector_root=tmp_path / "detector",
        built_in_preset_root=built_in,
        user_lut_root=user,
    )


def _initialize_phase2c(settings):
    initialize_phase2b(settings)
    apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )
    backup_path = settings.database_path.with_name("standalone-backup.sqlite3")
    source = sqlite3.connect(settings.database_path)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    backup_path.replace(settings.database_path)
    Path(f"{settings.database_path}-wal").unlink(missing_ok=True)
    Path(f"{settings.database_path}-shm").unlink(missing_ok=True)


def _migrate(settings, *, mode="apply", **kwargs):
    return apply_detector_v2_migration(
        settings=settings,
        mode=mode,
        offline_maintenance_confirmed=True,
        api_stopped_confirmed=True,
        release_040_ready_confirmed=True,
        isolated_database_confirmed=mode == "dry-run",
        release_readiness_check=lambda _settings: True,
        **kwargs,
    )


def test_detector_v2_preflight_only_is_read_only(tmp_path):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)
    before = settings.database_path.read_bytes()

    result = apply_detector_v2_migration(settings=settings)

    assert result.status == "preflight_ready"
    assert settings.database_path.read_bytes() == before
    with connect(settings.database_path, 5000) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA legacy_alter_table").fetchone()[0] == 0
        assert resolve_managed_phase_schema(conn).detector_v2_present is False


def _sidecar_snapshot(database_path: Path):
    result = {}
    for path in (
        database_path,
        Path(f"{database_path}-wal"),
        Path(f"{database_path}-shm"),
    ):
        if not path.exists():
            result[path.name] = None
            continue
        stat = path.stat()
        result[path.name] = (
            stat.st_mode,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
            sha256(path.read_bytes()).hexdigest(),
        )
    return result


def test_detector_v2_preflight_does_not_create_or_change_main_wal_shm(tmp_path):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)
    wal = Path(f"{settings.database_path}-wal")
    shm = Path(f"{settings.database_path}-shm")
    assert not wal.exists()
    assert not shm.exists()
    before = _sidecar_snapshot(settings.database_path)

    result = apply_detector_v2_migration(settings=settings)

    assert result.status == "preflight_ready"
    assert _sidecar_snapshot(settings.database_path) == before


def test_detector_v2_preflight_preserves_existing_sidecar_bytes_and_metadata(tmp_path):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)
    wal = Path(f"{settings.database_path}-wal")
    shm = Path(f"{settings.database_path}-shm")
    wal.write_bytes(b"existing-wal-sentinel")
    shm.write_bytes(b"existing-shm-sentinel")
    before = _sidecar_snapshot(settings.database_path)

    with pytest.raises(
        DetectorV2MigrationError,
        match="detector_v2_preflight_read_only_open_failed",
    ):
        apply_detector_v2_migration(settings=settings)

    assert _sidecar_snapshot(settings.database_path) == before


def test_detector_v2_preflight_rejects_broken_sidecar_symlink_without_change(tmp_path):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)
    wal = Path(f"{settings.database_path}-wal")
    wal.symlink_to(tmp_path / "missing-sidecar-target")
    before_database = settings.database_path.read_bytes()

    with pytest.raises(
        DetectorV2MigrationError,
        match="detector_v2_preflight_read_only_open_failed",
    ):
        apply_detector_v2_migration(settings=settings)

    assert wal.is_symlink()
    assert settings.database_path.read_bytes() == before_database


def test_detector_v2_preflight_does_not_create_missing_parent(tmp_path):
    settings = _settings(tmp_path)
    missing_parent = tmp_path / "missing" / "nested"
    settings = Settings(
        media_root=settings.media_root,
        api_token=settings.api_token,
        database_path=missing_parent / "db.sqlite3",
        detector_root=settings.detector_root,
        built_in_preset_root=settings.built_in_preset_root,
        user_lut_root=settings.user_lut_root,
    )

    with pytest.raises(
        DetectorV2MigrationError,
        match="detector_v2_preflight_read_only_open_failed",
    ):
        apply_detector_v2_migration(settings=settings)

    assert not missing_parent.exists()


def test_detector_v2_dry_run_rolls_back_full_successor(tmp_path):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)

    result = _migrate(settings, mode="dry-run")

    assert result.status == "dry_run"
    with connect(settings.database_path, 5000) as conn:
        state = resolve_managed_phase_schema(conn)
        assert state.phase2c_valid is True
        assert state.detector_v2_present is False
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA legacy_alter_table").fetchone()[0] == 0


def test_detector_v2_apply_has_exact_schema_identity_and_is_idempotent(tmp_path):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "INSERT INTO assets (type, filename) VALUES ('video', 'fixture.mov')"
        )
        conn.commit()

    result = _migrate(settings)
    repeated = _migrate(settings)

    assert result.status == "applied"
    assert repeated.status == "already_applied"
    with connect(settings.database_path, 5000) as conn:
        state = resolve_managed_phase_schema(conn)
        marker = conn.execute(
            "SELECT version FROM detector_v2_schema_metadata"
        ).fetchone()[0]
        assert state.detector_v2_valid is True
        assert state.minimum_client_version == "0.4.0"
        assert marker == DETECTOR_V2_MIGRATION_VERSION
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1


def test_detector_v2_rejects_incompatible_existing_apple_log_row(tmp_path):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            """
            INSERT INTO assets (
                type, filename, log_detection_status,
                detector_rule_version, detector_manifest_sha256,
                detector_evidence_sha256
            ) VALUES ('video', 'legacy.mov', 'apple_log', 'v1', ?, ?)
            """,
            ("a" * 64, "b" * 64),
        )
        conn.commit()

    with pytest.raises(
        DetectorV2MigrationError,
        match="detector_v2_existing_rows_incompatible",
    ):
        _migrate(settings)

    with connect(settings.database_path, 5000) as conn:
        assert resolve_managed_phase_schema(conn).detector_v2_present is False


def test_detector_v2_rejects_existing_applied_row_without_rewriting_it(tmp_path):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            """
            INSERT INTO assets (
                type, filename, log_detection_status, source_profile,
                detector_rule_version, detector_manifest_sha256,
                detector_evidence_sha256
            ) VALUES ('video', 'applied.mov', 'apple_log', 'apple-log-1',
                      'rule-v1', ?, ?)
            """,
            ("a" * 64, "b" * 64),
        )
        asset_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        job_id = conn.execute(
            """
            INSERT INTO jobs (job_type, status, asset_id, payload_json, dedup_key,
                              preview_generation)
            VALUES ('preview', 'done', ?, '{}', 'applied-row', 1)
            """,
            (asset_id,),
        ).lastrowid
        conn.execute(
            "UPDATE assets SET preview_generation = 1 WHERE id = ?",
            (asset_id,),
        )
        conn.execute(
            """
            INSERT INTO formal_preview_attempts (
                id, asset_id, job_id, preview_generation, state,
                detection_status, source_profile, detector_rule_version,
                detector_manifest_sha256, detector_evidence_sha256,
                detector_evidence_json, requested_preset_id,
                registry_classification, applied_preset_id,
                preset_display_name, preset_kind, preset_version,
                manifest_sha256, expected_lut_sha256, transform_kind,
                color_transform_status, terminal_at
            ) VALUES (?, ?, ?, 1, 'superseded', 'apple_log', 'apple-log-1',
                      'rule-v1', ?, ?, '{}', 'generated-apple-log-rec709',
                      'valid', 'generated-apple-log-rec709', 'Future transform',
                      'lut', 'future-1', ?, ?, 'lut', 'applied', CURRENT_TIMESTAMP)
            """,
            (
                "1" * 32,
                asset_id,
                job_id,
                "a" * 64,
                "b" * 64,
                "c" * 64,
                "d" * 64,
            ),
        )
        before = dict(
            conn.execute(
                "SELECT * FROM formal_preview_attempts WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
        )
        conn.commit()

    with pytest.raises(
        DetectorV2MigrationError,
        match="detector_v2_existing_apple_log_applied_not_allowed",
    ):
        _migrate(settings)

    with connect(settings.database_path, 5000) as conn:
        after = dict(
            conn.execute(
                "SELECT * FROM formal_preview_attempts WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
        )
        assert after == before
        assert after["color_transform_status"] == "applied"
        assert resolve_managed_phase_schema(conn).detector_v2_present is False


def _table_structure(conn, table):
    indexes = []
    for row in conn.execute(f'PRAGMA index_list("{table}")'):
        indexes.append(
            (
                tuple(row),
                tuple(
                    tuple(index_row)
                    for index_row in conn.execute(f'PRAGMA index_xinfo("{row[1]}")')
                ),
            )
        )
    return {
        "columns": tuple(
            tuple(row) for row in conn.execute(f'PRAGMA table_info("{table}")')
        ),
        "foreign_keys": tuple(
            tuple(row) for row in conn.execute(f'PRAGMA foreign_key_list("{table}")')
        ),
        "indexes": tuple(indexes),
    }


def test_detector_v2_preserves_all_rebuilt_table_structure_rows_and_sequence(tmp_path):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "INSERT INTO assets (id, type, filename) VALUES (7, 'video', 'fixture.mov')"
        )
        conn.commit()
        before_structure = {
            table: _table_structure(conn, table)
            for table in ("assets", "formal_preview_attempts", "preview_provenance")
        }
        before_rows = {
            table: tuple(
                tuple(row)
                for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
            )
            for table in ("assets", "formal_preview_attempts", "preview_provenance")
        }
        before_sequence = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'assets'"
        ).fetchone()[0]

    _migrate(settings)

    with connect(settings.database_path, 5000) as conn:
        after_structure = {
            table: _table_structure(conn, table)
            for table in ("assets", "formal_preview_attempts", "preview_provenance")
        }
        after_rows = {
            table: tuple(
                tuple(row)
                for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
            )
            for table in ("assets", "formal_preview_attempts", "preview_provenance")
        }
        after_sequence = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'assets'"
        ).fetchone()[0]
    assert after_structure == before_structure
    assert after_rows == before_rows
    assert after_sequence == before_sequence == 7


def test_detector_v2_recreates_exact_successor_trigger_set(tmp_path):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)
    _migrate(settings)

    with connect(settings.database_path, 5000) as conn:
        actual = {
            row["name"]: sha256(row["sql"].encode("utf-8")).hexdigest()
            for row in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
            )
            if row["name"] in DETECTOR_V2_REFERENCING_TRIGGER_NAMES
        }
        ready_sql = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'trigger' AND name = 'validate_formal_preview_ready'
            """
        ).fetchone()[0]
    assert actual == DETECTOR_V2_TRIGGER_SQL_SHA256
    assert "apple-log-1" in ready_sql
    assert "apple-log-2" in ready_sql
    assert "generated-apple-log2-rec709" in ready_sql


class _RestoreFailureConnection:
    def execute(self, _statement):
        raise sqlite3.OperationalError("injected private detail")


def test_detector_v2_pragma_restore_failure_has_stable_code():
    with pytest.raises(
        DetectorV2MigrationError,
        match="^detector_v2_migration_pragma_restore_failed$",
    ):
        _restore_default_pragmas(_RestoreFailureConnection())


@pytest.mark.parametrize(
    "preset_id",
    ["generated-apple-log-rec709", "generated-apple-log2-rec709"],
)
@pytest.mark.parametrize(
    ("classification", "expected_error"),
    [
        ("absent", None),
        ("disabled", None),
        ("registered_invalid", "detector_v2_reserved_preset_not_disabled"),
        ("valid", "detector_v2_reserved_preset_not_disabled"),
        ("reserved_namespace_collision", "detector_v2_reserved_namespace_collision"),
    ],
)
def test_detector_v2_reserved_preset_preflight_matrix(
    tmp_path,
    preset_id,
    classification,
    expected_error,
):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)
    if classification == "disabled":
        write_custom(settings.user_lut_root, preset_id, enabled=False)
    elif classification == "registered_invalid":
        (settings.user_lut_root / preset_id).mkdir()
    elif classification == "valid":
        write_custom(settings.user_lut_root, preset_id, enabled=True)
    elif classification == "reserved_namespace_collision":
        (settings.built_in_preset_root / preset_id).mkdir()

    if expected_error is None:
        result = apply_detector_v2_migration(settings=settings)
        assert result.status == "preflight_ready"
    else:
        with pytest.raises(DetectorV2MigrationError, match=f"^{expected_error}$"):
            apply_detector_v2_migration(settings=settings)

    with connect(settings.database_path, 5000) as conn:
        assert resolve_managed_phase_schema(conn).detector_v2_present is False
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA legacy_alter_table").fetchone()[0] == 0


def test_detector_v2_fault_rolls_back_schema_marker_rows_and_pragmas(tmp_path):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)

    def fail(step):
        if step == "after_marker":
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        _migrate(settings, fault_injector=fail)

    with connect(settings.database_path, 5000) as conn:
        state = resolve_managed_phase_schema(conn)
        assert state.detector_v2_present is False
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA legacy_alter_table").fetchone()[0] == 0
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("SELECT * FROM detector_v2_schema_metadata")


@pytest.mark.parametrize(
    "step",
    [
        "after_trigger_drop",
        "after_assets_copy",
        "after_formal_preview_attempts_copy",
        "after_preview_provenance_copy",
        "after_schema_rebuild",
        "after_marker",
        "after_integrity",
        "after_schema_identity",
    ],
)
def test_detector_v2_migration_fault_matrix_completely_rolls_back(tmp_path, step):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)
    with connect(settings.database_path, 5000) as conn:
        before_objects = conn.execute(
            """
            SELECT type, name, sql FROM sqlite_master
            WHERE sql IS NOT NULL ORDER BY type, name
            """
        ).fetchall()

    def fail(actual):
        if actual == step:
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        _migrate(settings, fault_injector=fail)

    with connect(settings.database_path, 5000) as conn:
        after_objects = conn.execute(
            """
            SELECT type, name, sql FROM sqlite_master
            WHERE sql IS NOT NULL ORDER BY type, name
            """
        ).fetchall()
        assert [tuple(row) for row in after_objects] == [
            tuple(row) for row in before_objects
        ]
        assert resolve_managed_phase_schema(conn).phase2c_valid is True
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA legacy_alter_table").fetchone()[0] == 0


@pytest.mark.parametrize(
    "step",
    ["after_locked_preflight", "after_schema_rebuild", "after_marker"],
)
@pytest.mark.parametrize(
    "preset_id",
    ["generated-apple-log-rec709", "generated-apple-log2-rec709"],
)
def test_reserved_preset_creation_race_rolls_back_successor(
    tmp_path,
    step,
    preset_id,
):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "INSERT INTO assets (id, type, filename) VALUES (9, 'video', 'fixture.mov')"
        )
        conn.commit()
        before_objects = tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT type, name, sql FROM sqlite_master
                WHERE sql IS NOT NULL ORDER BY type, name
                """
            )
        )
        before_rows = tuple(
            tuple(row) for row in conn.execute("SELECT * FROM assets ORDER BY id")
        )
        before_sequence = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'assets'"
        ).fetchone()[0]

    def mutate(actual):
        if actual == step:
            candidate = settings.built_in_preset_root / preset_id
            candidate.mkdir()

    with pytest.raises(
        DetectorV2MigrationError,
        match="detector_v2_reserved_preset_changed",
    ):
        _migrate(settings, fault_injector=mutate)

    with connect(settings.database_path, 5000) as conn:
        assert resolve_managed_phase_schema(conn).detector_v2_present is False
        after_objects = tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT type, name, sql FROM sqlite_master
                WHERE sql IS NOT NULL ORDER BY type, name
                """
            )
        )
        after_rows = tuple(
            tuple(row) for row in conn.execute("SELECT * FROM assets ORDER BY id")
        )
        after_sequence = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'assets'"
        ).fetchone()[0]
        assert after_objects == before_objects
        assert after_rows == before_rows
        assert after_sequence == before_sequence == 9
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA legacy_alter_table").fetchone()[0] == 0


@pytest.mark.parametrize(
    "step",
    ["after_locked_preflight", "after_schema_rebuild", "after_marker"],
)
@pytest.mark.parametrize(
    "preset_id",
    ["generated-apple-log-rec709", "generated-apple-log2-rec709"],
)
@pytest.mark.parametrize("mutation", ["candidate_replace", "manifest_change"])
def test_reserved_disabled_preset_identity_race_rolls_back_successor(
    tmp_path,
    step,
    preset_id,
    mutation,
):
    settings = _settings(tmp_path)
    _initialize_phase2c(settings)
    candidate = write_custom(settings.user_lut_root, preset_id, enabled=False)

    def mutate(actual):
        if actual != step:
            return
        if mutation == "candidate_replace":
            replacement = settings.user_lut_root / f"{preset_id}-replacement"
            write_custom(
                settings.user_lut_root, f"{preset_id}-replacement", enabled=False
            )
            candidate.rename(settings.user_lut_root / f"{preset_id}-old")
            replacement.rename(candidate)
        else:
            manifest = candidate / "manifest.json"
            manifest.write_bytes(manifest.read_bytes() + b" ")

    with pytest.raises(
        DetectorV2MigrationError,
        match="detector_v2_reserved_preset_changed",
    ):
        _migrate(settings, fault_injector=mutate)

    with connect(settings.database_path, 5000) as conn:
        assert resolve_managed_phase_schema(conn).detector_v2_present is False
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA legacy_alter_table").fetchone()[0] == 0


@pytest.mark.parametrize("target", ["formal_preview_attempts", "preview_provenance"])
def test_detector_v2_rejects_each_incompatible_profile_preset_row(
    tmp_path,
    target,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        from tests.phase2c_test_support import insert_eligible_confirmed_asset

        insert_eligible_confirmed_asset(conn)
        conn.commit()
    apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "UPDATE assets SET delete_candidate_status = 'not_candidate' WHERE id = 1"
        )
        trigger_name = (
            "prevent_terminal_formal_preview_attempt_update"
            if target == "formal_preview_attempts"
            else "prevent_preview_provenance_update"
        )
        trigger_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            (trigger_name,),
        ).fetchone()[0]
        conn.execute(f'DROP TRIGGER "{trigger_name}"')
        if target == "preview_provenance":
            conn.execute(
                """
                UPDATE preview_provenance
                SET detection_status = 'apple_log',
                    source_profile = 'apple-log-2',
                    requested_preset_id = 'generated-apple-log-rec709',
                    preset_version = NULL,
                    transform_kind = 'none',
                    color_transform_status = 'unavailable',
                    color_transform_error_code = 'lut_preset_unavailable'
                WHERE asset_id = 1
                """
            )
        else:
            conn.execute(
                """
                UPDATE formal_preview_attempts
                SET detection_status = 'apple_log',
                    source_profile = 'apple-log-2',
                    requested_preset_id = 'generated-apple-log-rec709'
                WHERE asset_id = 1
                """
            )
        conn.execute(trigger_sql)
        before = tuple(tuple(row) for row in conn.execute(f'SELECT * FROM "{target}"'))
        conn.commit()

    with pytest.raises(
        DetectorV2MigrationError,
        match="detector_v2_existing_rows_incompatible",
    ):
        _migrate(settings)

    with connect(settings.database_path, 5000) as conn:
        after = tuple(tuple(row) for row in conn.execute(f'SELECT * FROM "{target}"'))
        assert after == before
        assert resolve_managed_phase_schema(conn).detector_v2_present is False
