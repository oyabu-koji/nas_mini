from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from app.core.settings import (
    MAX_UPLOAD_CHUNKS,
    MAX_UPLOAD_CHUNK_SIZE_BYTES,
    MAX_UPLOAD_SESSION_SIZE_BYTES,
    Settings,
)
from app.db.connection import connect
from app.db.phase2c import (
    EXPECTED_ASSETS_TABLE_SQL,
    EXPECTED_PREVIOUS_MIGRATION_VERSION,
    PHASE2C_MIGRATION_VERSION,
    assets_table_sql_sha256,
    execute_phase2c_sql,
    schema_sql_sha256,
)
from app.db.phase_schema_identity import (
    PhaseSchemaIdentityError,
    resolve_managed_phase_schema,
)
from app.services.detector_capability import evaluate_detector_runtime
from app.services.phase2b_drain import phase2b_drain_counts
from app.services.safe_delete_candidate import (
    evaluate_safe_delete_candidate,
    project_candidate_status,
)


class Phase2CMigrationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Phase2CMigrationResult:
    status: str
    promoted: int
    skipped: int
    reasons: dict[str, int]
    schema_sql_sha256: str
    assets_table_sql_sha256: str


RuntimeCheck = Callable[[Settings], bool]
FaultInjector = Callable[[str], None]


def apply_phase2c_migration(
    *,
    settings: Settings,
    offline_maintenance_confirmed: bool,
    dry_run: bool = False,
    runtime_check: RuntimeCheck | None = None,
    fault_injector: FaultInjector | None = None,
) -> Phase2CMigrationResult:
    if not offline_maintenance_confirmed:
        raise Phase2CMigrationError(
            "phase2c_migration_maintenance_confirmation_required"
        )
    check_runtime = runtime_check or _runtime_available

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        already_applied = _read_preflight(
            conn,
            settings=settings,
            runtime_check=check_runtime,
        )
        if already_applied:
            return _result(status="already_applied")
        _inject(fault_injector, "after_read_preflight")

        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
            raise Phase2CMigrationError(
                "phase2c_migration_foreign_keys_disable_failed"
            )
        try:
            conn.execute("BEGIN IMMEDIATE")
            if _locked_preflight(
                conn,
                settings=settings,
                runtime_check=check_runtime,
            ):
                conn.rollback()
                return _result(status="already_applied")
            _inject(fault_injector, "after_locked_preflight")
            execute_phase2c_sql(
                conn,
                fault_injector=fault_injector,
            )
            _inject(fault_injector, "after_schema")
            actual_assets_sql = _actual_assets_sql(conn)
            if actual_assets_sql != EXPECTED_ASSETS_TABLE_SQL:
                raise Phase2CMigrationError(
                    "phase2c_migration_assets_schema_identity_mismatch"
                )
            actual_assets_digest = assets_table_sql_sha256(actual_assets_sql)
            _inject(fault_injector, "after_assets_identity")
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (PHASE2C_MIGRATION_VERSION,),
            )
            _inject(fault_injector, "after_marker")
            conn.execute(
                """
                INSERT INTO phase2c_schema_metadata (
                    version, schema_sql_sha256, assets_table_sql_sha256
                ) VALUES (?, ?, ?)
                """,
                (
                    PHASE2C_MIGRATION_VERSION,
                    schema_sql_sha256(),
                    actual_assets_digest,
                ),
            )
            _inject(fault_injector, "after_metadata")
            promoted, skipped, reasons = _backfill(conn)
            _inject(fault_injector, "after_backfill")
            _require_integrity(conn)
            _inject(fault_injector, "after_integrity")
            if dry_run:
                conn.rollback()
                status = "dry_run"
            else:
                conn.commit()
                status = "applied"
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA legacy_alter_table = OFF")
            conn.execute("PRAGMA foreign_keys = ON")
    return _result(
        status=status,
        promoted=promoted,
        skipped=skipped,
        reasons=dict(reasons),
    )


def _read_preflight(
    conn: sqlite3.Connection,
    *,
    settings: Settings,
    runtime_check: RuntimeCheck,
) -> bool:
    try:
        state = resolve_managed_phase_schema(conn)
    except PhaseSchemaIdentityError as exc:
        raise Phase2CMigrationError(exc.code) from exc
    if state.phase2c_valid:
        return True
    if not state.phase2b_valid:
        raise Phase2CMigrationError("phase2c_migration_precondition_changed")
    _require_exact_predecessor(conn)
    _require_runtime(settings, runtime_check)
    _require_drained(conn)
    _require_upload_bounds(conn)
    return False


def _locked_preflight(
    conn: sqlite3.Connection,
    *,
    settings: Settings,
    runtime_check: RuntimeCheck,
) -> bool:
    return _read_preflight(
        conn,
        settings=settings,
        runtime_check=runtime_check,
    )


def _require_exact_predecessor(conn: sqlite3.Connection) -> None:
    latest = conn.execute(
        """
        SELECT version FROM schema_migrations
        WHERE version >= '008_'
        ORDER BY version DESC
        LIMIT 1
        """
    ).fetchone()
    if latest is None or latest["version"] != EXPECTED_PREVIOUS_MIGRATION_VERSION:
        raise Phase2CMigrationError("phase2c_migration_precondition_changed")


def _require_runtime(settings: Settings, runtime_check: RuntimeCheck) -> None:
    if not runtime_check(settings):
        raise Phase2CMigrationError(
            "phase2c_migration_phase2b_runtime_unavailable"
        )


def _runtime_available(settings: Settings) -> bool:
    return evaluate_detector_runtime(settings).detector_certified


def _require_drained(conn: sqlite3.Connection) -> None:
    counts = phase2b_drain_counts(conn)
    attempts = conn.execute(
        """
        SELECT COUNT(*) FROM formal_preview_attempts
        WHERE state IN ('queued', 'probing', 'resolving', 'rendering', 'finalizing')
        """
    ).fetchone()[0]
    if not counts.drained or attempts:
        raise Phase2CMigrationError("phase2c_migration_preview_not_drained")


def _require_upload_bounds(conn: sqlite3.Connection) -> None:
    upload_count = conn.execute(
        """
        SELECT COUNT(*) FROM upload_sessions
        WHERE size_bytes < 1
           OR size_bytes > ?
           OR chunk_size_bytes < 1
           OR chunk_size_bytes > ?
        """,
        (MAX_UPLOAD_SESSION_SIZE_BYTES, MAX_UPLOAD_CHUNK_SIZE_BYTES),
    ).fetchone()[0]
    if upload_count:
        raise Phase2CMigrationError(
            "phase2c_migration_upload_limit_exceeded"
        )
    chunk_count = conn.execute(
        """
        SELECT COUNT(*) FROM upload_sessions
        WHERE (
            (size_bytes + chunk_size_bytes - 1) / chunk_size_bytes
        ) NOT BETWEEN 1 AND ?
        """,
        (MAX_UPLOAD_CHUNKS,),
    ).fetchone()[0]
    if chunk_count:
        raise Phase2CMigrationError(
            "phase2c_migration_chunk_limit_exceeded"
        )


def _backfill(conn: sqlite3.Connection) -> tuple[int, int, Counter]:
    promoted = 0
    skipped = 0
    reasons: Counter[str] = Counter()
    asset_ids = [
        int(row["id"])
        for row in conn.execute(
            """
            SELECT id FROM assets
            WHERE review_status = 'preview_confirmed'
            ORDER BY id
            """
        )
    ]
    for asset_id in asset_ids:
        evaluation = evaluate_safe_delete_candidate(conn, asset_id=asset_id)
        before = conn.execute(
            "SELECT delete_candidate_status FROM assets WHERE id = ?",
            (asset_id,),
        ).fetchone()["delete_candidate_status"]
        after = project_candidate_status(
            conn,
            asset_id=asset_id,
            evaluation=evaluation,
            allow_promotion=True,
        )
        if before != after and after == "safe_to_delete_candidate":
            promoted += 1
        else:
            skipped += 1
        if evaluation.reason is not None:
            reasons[evaluation.reason] += 1
    return promoted, skipped, reasons


def _require_integrity(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise Phase2CMigrationError("phase2c_migration_foreign_key_invalid")
    for row in conn.execute(
        """
        SELECT id FROM assets
        WHERE delete_candidate_status = 'safe_to_delete_candidate'
        """
    ):
        if not evaluate_safe_delete_candidate(
            conn,
            asset_id=int(row["id"]),
        ).eligible:
            raise Phase2CMigrationError(
                "phase2c_migration_candidate_integrity_invalid"
            )


def _actual_assets_sql(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'assets'
        """
    ).fetchone()
    if row is None or not isinstance(row["sql"], str):
        raise Phase2CMigrationError(
            "phase2c_migration_assets_schema_identity_mismatch"
        )
    return row["sql"]


def _result(
    *,
    status: str,
    promoted: int = 0,
    skipped: int = 0,
    reasons: dict[str, int] | None = None,
) -> Phase2CMigrationResult:
    return Phase2CMigrationResult(
        status=status,
        promoted=promoted,
        skipped=skipped,
        reasons=reasons or {},
        schema_sql_sha256=schema_sql_sha256(),
        assets_table_sql_sha256=assets_table_sql_sha256(),
    )


def _inject(injector: FaultInjector | None, step: str) -> None:
    if injector is not None:
        injector(step)
