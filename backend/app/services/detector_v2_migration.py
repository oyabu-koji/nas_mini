from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.core.settings import Settings
from app.db.connection import connect
from app.db.detector_v2 import (
    DETECTOR_V2_MIGRATION_VERSION,
    EXPECTED_PREVIOUS_MIGRATION_VERSION,
    EXPECTED_PREVIOUS_SCHEMA_SHA256,
    predecessor_schema_matches,
)
from app.db.detector_v2.schema import (
    DETECTOR_V2_METADATA_TABLE_SQL,
    DETECTOR_V2_REFERENCING_TRIGGER_NAMES,
    EXPECTED_ASSETS_ORIGINAL_PATH_INDEX_SQL,
    EXPECTED_DETECTOR_V2_TRIGGER_SQL,
    EXPECTED_FORMAL_ATTEMPTS_INDEX_SQL,
    assets_rebuild_table_sql,
    detector_v2_schema_identity_sha256,
    formal_preview_attempts_rebuild_table_sql,
    preview_provenance_rebuild_table_sql,
)
from app.db.phase_schema_identity import (
    PhaseSchemaIdentityError,
    resolve_managed_phase_schema,
)
from app.services.detector_capability import evaluate_detector_runtime
from app.services.phase2b_drain import phase2b_drain_counts
from app.services.preset_registry import (
    RESERVED_PROFILE_PRESET_PAIRS,
    ReservedPresetRegistryIdentity,
    classify_reserved_preset_with_identity,
)


MigrationMode = Literal["preflight-only", "dry-run", "apply"]
FaultInjector = Callable[[str], None]
ReleaseReadinessCheck = Callable[[Settings], bool]


class DetectorV2MigrationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DetectorV2MigrationResult:
    status: Literal["preflight_ready", "dry_run", "applied", "already_applied"]
    schema_identity_sha256: str


def apply_detector_v2_migration(
    *,
    settings: Settings,
    mode: MigrationMode = "preflight-only",
    offline_maintenance_confirmed: bool = False,
    api_stopped_confirmed: bool = False,
    release_040_ready_confirmed: bool = False,
    isolated_database_confirmed: bool = False,
    release_readiness_check: ReleaseReadinessCheck | None = None,
    fault_injector: FaultInjector | None = None,
) -> DetectorV2MigrationResult:
    if mode not in {"preflight-only", "dry-run", "apply"}:
        raise DetectorV2MigrationError("detector_v2_migration_mode_invalid")
    if mode == "dry-run":
        if not isolated_database_confirmed:
            raise DetectorV2MigrationError(
                "detector_v2_migration_isolated_database_confirmation_required"
            )
        if settings.database_path == Path("/data/mediavault.sqlite3"):
            raise DetectorV2MigrationError(
                "detector_v2_migration_operator_database_dry_run_not_allowed"
            )
    if mode == "apply" and not (
        offline_maintenance_confirmed
        and api_stopped_confirmed
        and release_040_ready_confirmed
    ):
        raise DetectorV2MigrationError(
            "detector_v2_migration_apply_confirmation_required"
        )

    readiness = release_readiness_check or _release_040_ready
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        _require_initial_connection_state(conn)
        already_applied, read_snapshot = _read_preflight(
            conn,
            settings=settings,
            require_release_ready=mode != "preflight-only",
            release_readiness_check=readiness,
        )
        if already_applied:
            return _result("already_applied")
        if mode == "preflight-only":
            _require_default_pragmas(conn)
            return _result("preflight_ready")
        _inject(fault_injector, "after_read_preflight")

        transaction_started = False
        try:
            _set_migration_pragmas(conn)
            conn.execute("BEGIN IMMEDIATE")
            transaction_started = True
            try:
                already_applied, locked_snapshot = _read_preflight(
                    conn,
                    settings=settings,
                    require_release_ready=True,
                    release_readiness_check=readiness,
                )
            except DetectorV2MigrationError as exc:
                if exc.code in {
                    "detector_v2_reserved_namespace_collision",
                    "detector_v2_reserved_preset_not_disabled",
                }:
                    raise DetectorV2MigrationError(
                        "detector_v2_reserved_preset_changed"
                    ) from exc
                raise
            if already_applied:
                conn.rollback()
                transaction_started = False
                return _result("already_applied")
            if locked_snapshot != read_snapshot:
                raise DetectorV2MigrationError(
                    "detector_v2_reserved_preset_changed"
                )
            _inject(fault_injector, "after_locked_preflight")
            _rebuild_successor_schema(conn, fault_injector=fault_injector)
            _insert_successor_markers(conn)
            _inject(fault_injector, "after_marker")
            _require_integrity(conn)
            _inject(fault_injector, "after_integrity")
            _require_successor_identity(conn)
            _inject(fault_injector, "after_schema_identity")
            try:
                final_snapshot = _reserved_preset_snapshot(settings)
            except DetectorV2MigrationError as exc:
                if exc.code in {
                    "detector_v2_reserved_namespace_collision",
                    "detector_v2_reserved_preset_not_disabled",
                }:
                    raise DetectorV2MigrationError(
                        "detector_v2_reserved_preset_changed"
                    ) from exc
                raise
            if final_snapshot != locked_snapshot:
                raise DetectorV2MigrationError(
                    "detector_v2_reserved_preset_changed"
                )
            if mode == "dry-run":
                conn.rollback()
                status = "dry_run"
            else:
                conn.commit()
                status = "applied"
            transaction_started = False
        except Exception:
            if transaction_started and conn.in_transaction:
                conn.rollback()
            raise
        finally:
            _restore_default_pragmas(conn)
    return _result(status)


def _read_preflight(
    conn: sqlite3.Connection,
    *,
    settings: Settings,
    require_release_ready: bool,
    release_readiness_check: ReleaseReadinessCheck,
) -> tuple[bool, tuple[ReservedPresetRegistryIdentity, ...]]:
    try:
        state = resolve_managed_phase_schema(conn)
    except PhaseSchemaIdentityError as exc:
        raise DetectorV2MigrationError(exc.code) from exc
    if state.detector_v2_valid:
        return True, ()
    if not state.phase2c_valid or not predecessor_schema_matches():
        raise DetectorV2MigrationError(
            "detector_v2_migration_precondition_changed"
        )
    latest = conn.execute(
        """
        SELECT version FROM schema_migrations
        WHERE version >= '008_'
        ORDER BY version DESC LIMIT 1
        """
    ).fetchone()
    if latest is None or latest["version"] != EXPECTED_PREVIOUS_MIGRATION_VERSION:
        raise DetectorV2MigrationError(
            "detector_v2_migration_precondition_changed"
        )
    _require_drained(conn)
    _require_existing_rows_compatible(conn)
    snapshot = _reserved_preset_snapshot(settings)
    if require_release_ready and not release_readiness_check(settings):
        raise DetectorV2MigrationError(
            "detector_v2_migration_release_040_not_ready"
        )
    return False, snapshot


def _reserved_preset_snapshot(
    settings: Settings,
) -> tuple[ReservedPresetRegistryIdentity, ...]:
    identities = tuple(
        classify_reserved_preset_with_identity(settings, preset_id)
        for _profile, preset_id in RESERVED_PROFILE_PRESET_PAIRS
    )
    for identity in identities:
        if identity.classification == "reserved_namespace_collision":
            raise DetectorV2MigrationError(
                "detector_v2_reserved_namespace_collision"
            )
        if identity.classification not in {"absent", "disabled"}:
            raise DetectorV2MigrationError(
                "detector_v2_reserved_preset_not_disabled"
            )
    return identities


def _require_existing_rows_compatible(conn: sqlite3.Connection) -> None:
    applied = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM formal_preview_attempts
             WHERE detection_status = 'apple_log'
               AND (applied_preset_id <> 'compress-only'
                    OR transform_kind = 'lut'
                    OR color_transform_status = 'applied'
                    OR manifest_sha256 IS NOT NULL
                    OR expected_lut_sha256 IS NOT NULL))
            +
            (SELECT COUNT(*) FROM preview_provenance
             WHERE detection_status = 'apple_log'
               AND (applied_preset_id <> 'compress-only'
                    OR transform_kind = 'lut'
                    OR color_transform_status = 'applied'
                    OR manifest_sha256 IS NOT NULL
                    OR lut_sha256 IS NOT NULL))
        """
    ).fetchone()[0]
    if applied:
        raise DetectorV2MigrationError(
            "detector_v2_existing_apple_log_applied_not_allowed"
        )
    invalid_assets = conn.execute(
        """
        SELECT COUNT(*) FROM assets
        WHERE CASE
            WHEN log_detection_status = 'apple_log'
             AND source_profile IN ('apple-log-1', 'apple-log-2') THEN 0
            WHEN log_detection_status IN ('not_evaluated', 'not_log', 'unknown')
             AND source_profile IS NULL THEN 0
            ELSE 1
        END = 1
        """
    ).fetchone()[0]
    invalid_attempts = conn.execute(
        """
        SELECT COUNT(*) FROM formal_preview_attempts
        WHERE CASE
            WHEN detection_status IS NULL AND source_profile IS NULL
             AND requested_preset_id IS NULL THEN 0
            WHEN detection_status = 'apple_log'
             AND source_profile = 'apple-log-1'
             AND (requested_preset_id IS NULL OR
                  requested_preset_id = 'generated-apple-log-rec709') THEN 0
            WHEN detection_status = 'apple_log'
             AND source_profile = 'apple-log-2'
             AND (requested_preset_id IS NULL OR
                  requested_preset_id = 'generated-apple-log2-rec709') THEN 0
            WHEN detection_status IN ('not_log', 'unknown')
             AND source_profile IS NULL
             AND (requested_preset_id IS NULL OR
                  requested_preset_id = 'compress-only') THEN 0
            ELSE 1
        END = 1
        """
    ).fetchone()[0]
    invalid_provenance = conn.execute(
        """
        SELECT COUNT(*) FROM preview_provenance
        WHERE CASE
            WHEN detection_status = 'apple_log'
             AND source_profile = 'apple-log-1'
             AND requested_preset_id = 'generated-apple-log-rec709'
             AND applied_preset_id = 'compress-only'
             AND transform_kind = 'none'
             AND color_transform_status = 'unavailable' THEN 0
            WHEN detection_status = 'apple_log'
             AND source_profile = 'apple-log-2'
             AND requested_preset_id = 'generated-apple-log2-rec709'
             AND applied_preset_id = 'compress-only'
             AND transform_kind = 'none'
             AND color_transform_status = 'unavailable' THEN 0
            WHEN detection_status IN ('not_log', 'unknown')
             AND source_profile IS NULL
             AND requested_preset_id = 'compress-only'
             AND applied_preset_id = 'compress-only'
             AND transform_kind = 'none'
             AND color_transform_status = 'not_requested' THEN 0
            ELSE 1
        END = 1
        """
    ).fetchone()[0]
    if invalid_assets or invalid_attempts or invalid_provenance:
        raise DetectorV2MigrationError(
            "detector_v2_existing_rows_incompatible"
        )


def _require_drained(conn: sqlite3.Connection) -> None:
    counts = phase2b_drain_counts(conn)
    attempts = conn.execute(
        """
        SELECT COUNT(*) FROM formal_preview_attempts
        WHERE state IN ('queued', 'probing', 'resolving', 'rendering', 'finalizing')
        """
    ).fetchone()[0]
    if not counts.drained or attempts:
        raise DetectorV2MigrationError(
            "detector_v2_migration_preview_not_drained"
        )


def _rebuild_successor_schema(
    conn: sqlite3.Connection,
    *,
    fault_injector: FaultInjector | None,
) -> None:
    counts = {
        table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in ("assets", "formal_preview_attempts", "preview_provenance")
    }
    sequence_row = conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = 'assets'"
    ).fetchone()
    sequence = sequence_row[0] if sequence_row is not None else None
    for name in sorted(DETECTOR_V2_REFERENCING_TRIGGER_NAMES):
        conn.execute(f'DROP TRIGGER "{name}"')
    _inject(fault_injector, "after_trigger_drop")

    rebuilds = (
        ("assets", "assets_detector_v2_new", assets_rebuild_table_sql()),
        (
            "formal_preview_attempts",
            "formal_preview_attempts_detector_v2_new",
            formal_preview_attempts_rebuild_table_sql(),
        ),
        (
            "preview_provenance",
            "preview_provenance_detector_v2_new",
            preview_provenance_rebuild_table_sql(),
        ),
    )
    for table, temporary, create_sql in rebuilds:
        conn.execute(create_sql)
        columns = [
            row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')
        ]
        quoted = ", ".join(f'"{column}"' for column in columns)
        conn.execute(
            f'INSERT INTO "{temporary}" ({quoted}) '
            f'SELECT {quoted} FROM "{table}"'
        )
        if conn.execute(f'SELECT COUNT(*) FROM "{temporary}"').fetchone()[0] != counts[table]:
            raise DetectorV2MigrationError(
                "detector_v2_migration_row_count_mismatch"
            )
        _inject(fault_injector, f"after_{table}_copy")

    for table, temporary, _create_sql in reversed(rebuilds):
        old = f"{table}_detector_v2_old"
        conn.execute(f'ALTER TABLE "{table}" RENAME TO "{old}"')
        conn.execute(f'ALTER TABLE "{temporary}" RENAME TO "{table}"')
        conn.execute(f'DROP TABLE "{old}"')
    conn.execute(EXPECTED_ASSETS_ORIGINAL_PATH_INDEX_SQL)
    conn.execute(EXPECTED_FORMAL_ATTEMPTS_INDEX_SQL)
    if sequence is not None:
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name LIKE '%detector_v2%' OR name = 'assets'"
        )
        conn.execute(
            "INSERT INTO sqlite_sequence (name, seq) VALUES ('assets', ?)",
            (sequence,),
        )
    for name in sorted(EXPECTED_DETECTOR_V2_TRIGGER_SQL):
        conn.execute(EXPECTED_DETECTOR_V2_TRIGGER_SQL[name])
    _inject(fault_injector, "after_schema_rebuild")


def _insert_successor_markers(conn: sqlite3.Connection) -> None:
    conn.execute(DETECTOR_V2_METADATA_TABLE_SQL)
    conn.execute(
        "INSERT INTO schema_migrations (version) VALUES (?)",
        (DETECTOR_V2_MIGRATION_VERSION,),
    )
    conn.execute(
        """
        INSERT INTO detector_v2_schema_metadata (
            version, predecessor_schema_sha256, schema_identity_sha256
        ) VALUES (?, ?, ?)
        """,
        (
            DETECTOR_V2_MIGRATION_VERSION,
            EXPECTED_PREVIOUS_SCHEMA_SHA256,
            detector_v2_schema_identity_sha256(),
        ),
    )


def _require_integrity(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise DetectorV2MigrationError(
            "detector_v2_migration_foreign_key_invalid"
        )
    for table in ("formal_preview_attempts", "preview_provenance"):
        if conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE asset_id NOT IN '
            "(SELECT id FROM assets)"
        ).fetchone()[0]:
            raise DetectorV2MigrationError(
                "detector_v2_migration_key_relation_invalid"
            )


def _require_successor_identity(conn: sqlite3.Connection) -> None:
    try:
        state = resolve_managed_phase_schema(conn)
    except PhaseSchemaIdentityError as exc:
        raise DetectorV2MigrationError(exc.code) from exc
    if not state.detector_v2_valid:
        raise DetectorV2MigrationError(
            "detector_v2_migration_schema_identity_mismatch"
        )


def _require_initial_connection_state(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise DetectorV2MigrationError(
            "detector_v2_migration_active_transaction"
        )
    _require_default_pragmas(conn)


def _require_default_pragmas(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise DetectorV2MigrationError(
            "detector_v2_migration_foreign_keys_not_enabled"
        )
    if conn.execute("PRAGMA legacy_alter_table").fetchone()[0] != 0:
        raise DetectorV2MigrationError(
            "detector_v2_migration_legacy_alter_table_enabled"
        )


def _set_migration_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA legacy_alter_table = ON")
    if (
        conn.execute("PRAGMA foreign_keys").fetchone()[0] != 0
        or conn.execute("PRAGMA legacy_alter_table").fetchone()[0] != 1
    ):
        raise DetectorV2MigrationError(
            "detector_v2_migration_pragma_switch_failed"
        )


def _restore_default_pragmas(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")
        _require_default_pragmas(conn)
    except (sqlite3.DatabaseError, DetectorV2MigrationError) as exc:
        raise DetectorV2MigrationError(
            "detector_v2_migration_pragma_restore_failed"
        ) from exc


def _release_040_ready(settings: Settings) -> bool:
    capability = evaluate_detector_runtime(settings)
    return bool(
        capability.detector_certified
        and capability.formal_apple_log_preview
    )


def _result(status) -> DetectorV2MigrationResult:
    return DetectorV2MigrationResult(
        status=status,
        schema_identity_sha256=detector_v2_schema_identity_sha256(),
    )


def _inject(injector: FaultInjector | None, step: str) -> None:
    if injector is not None:
        injector(step)
