from __future__ import annotations

import sqlite3
import json
from dataclasses import dataclass
from typing import Callable

from app.core.settings import Settings
from app.db.connection import connect
from app.db.phase2b import (
    EXPECTED_PREVIOUS_MIGRATION_VERSION,
    PHASE2B_MIGRATION_VERSION,
    PHASE2B_SQL_PATH,
    schema_sql_sha256,
)
from app.services.detector_capability import evaluate_detector_capability
from app.services.initial_release_guard import (
    assert_generated_apple_log_conversion_disabled,
)
from app.services.phase2b_drain import Phase2BDrainCounts, phase2b_drain_counts
from app.services.processed_result_authority import classify_active_processed_result


class Phase2BMigrationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Phase2BMigrationResult:
    status: str
    schema_sql_sha256: str


CertificationCheck = Callable[[Settings], None]
FaultInjector = Callable[[str], None]


def apply_phase2b_migration(
    *,
    settings: Settings,
    offline_maintenance_confirmed: bool,
    preflight_only: bool = False,
    certification_check: CertificationCheck | None = None,
    fault_injector: FaultInjector | None = None,
) -> Phase2BMigrationResult:
    if not offline_maintenance_confirmed:
        raise Phase2BMigrationError("phase2b_migration_maintenance_confirmation_required")
    check_certification = certification_check or _check_certification
    assert_generated_apple_log_conversion_disabled(settings)
    check_certification(settings)

    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        already_applied = _validate_schema_state(conn)
        _require_drained(phase2b_drain_counts(conn))
        if preflight_only:
            return Phase2BMigrationResult(
                status="already_applied" if already_applied else "preflight_ok",
                schema_sql_sha256=schema_sql_sha256(),
            )
        if already_applied:
            return Phase2BMigrationResult(
                status="already_applied",
                schema_sql_sha256=schema_sql_sha256(),
            )
        _inject(fault_injector, "after_read_preflight")

        try:
            conn.execute("BEGIN IMMEDIATE")
            if _validate_schema_state(conn):
                conn.rollback()
                return Phase2BMigrationResult(
                    status="already_applied",
                    schema_sql_sha256=schema_sql_sha256(),
                )
            _require_drained(phase2b_drain_counts(conn))
            _inject(fault_injector, "after_locked_preflight")
            _execute_phase2b_sql(conn, fault_injector=fault_injector)
            _backfill_eligible_assets(conn)
            _inject(fault_injector, "after_asset_backfill")
            conn.execute(
                """
                INSERT INTO phase2b_schema_metadata (version, schema_sql_sha256)
                VALUES (?, ?)
                """,
                (PHASE2B_MIGRATION_VERSION, schema_sql_sha256()),
            )
            _inject(fault_injector, "after_schema_identity")
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (PHASE2B_MIGRATION_VERSION,),
            )
            _inject(fault_injector, "after_marker")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise Phase2BMigrationError("phase2b_migration_foreign_key_invalid")
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
    return Phase2BMigrationResult(
        status="applied",
        schema_sql_sha256=schema_sql_sha256(),
    )


def _check_certification(settings: Settings) -> None:
    capability = evaluate_detector_capability(settings)
    if not capability.detector_certified:
        raise Phase2BMigrationError(
            capability.blocked_reason or "log_detector_manifest_invalid"
        )


def _validate_schema_state(conn: sqlite3.Connection) -> bool:
    versions = {
        row["version"]
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    if PHASE2B_MIGRATION_VERSION in versions:
        try:
            identity = conn.execute(
                """
                SELECT schema_sql_sha256
                FROM phase2b_schema_metadata
                WHERE version = ?
                """,
                (PHASE2B_MIGRATION_VERSION,),
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise Phase2BMigrationError("phase2b_migration_schema_identity_mismatch") from exc
        if identity is None or identity["schema_sql_sha256"] != schema_sql_sha256():
            raise Phase2BMigrationError("phase2b_migration_schema_identity_mismatch")
        return True
    if EXPECTED_PREVIOUS_MIGRATION_VERSION not in versions:
        raise Phase2BMigrationError("phase2b_migration_precondition_changed")
    if _table_exists(conn, "phase2b_schema_metadata"):
        raise Phase2BMigrationError("phase2b_migration_precondition_changed")
    return False


def _require_drained(counts: Phase2BDrainCounts) -> None:
    if not counts.drained:
        raise Phase2BMigrationError("phase2b_migration_preview_not_drained")


def _execute_phase2b_sql(
    conn: sqlite3.Connection, *, fault_injector: FaultInjector | None
) -> None:
    statement = ""
    statement_number = 0
    for line in PHASE2B_SQL_PATH.read_text(encoding="utf-8").splitlines(keepends=True):
        statement += line
        if not sqlite3.complete_statement(statement):
            continue
        sql = statement.strip()
        statement = ""
        if not sql:
            continue
        conn.execute(sql)
        statement_number += 1
        _inject(fault_injector, f"after_statement_{statement_number}")
    if statement.strip():
        raise Phase2BMigrationError("phase2b_migration_schema_invalid")


def _backfill_eligible_assets(conn: sqlite3.Connection) -> None:
    assets = conn.execute(
        """
        SELECT assets.*
        FROM assets
        JOIN upload_sessions
          ON upload_sessions.asset_id = assets.id
         AND upload_sessions.type = 'video'
         AND upload_sessions.status = 'completed'
        WHERE assets.type = 'video'
          AND assets.verification_status = 'file_verified'
          AND assets.preview_status IN ('preview_ready', 'failed')
        ORDER BY assets.id
        """
    ).fetchall()
    for asset in assets:
        dedup_key = f"phase2b-profile-preview:{asset['id']}"
        existing = conn.execute(
            "SELECT 1 FROM jobs WHERE dedup_key = ?", (dedup_key,)
        ).fetchone()
        if existing is not None:
            continue
        authority = classify_active_processed_result(conn, asset_id=int(asset["id"]))
        if authority.kind in {"ambiguous", "current_formal"}:
            raise Phase2BMigrationError("phase2b_migration_active_result_ambiguous")
        if authority.kind == "legacy_phase2a":
            conn.execute(
                "UPDATE assets SET active_processed_result_id = NULL WHERE id = ?",
                (asset["id"],),
            )
        conn.execute(
            """
            UPDATE assets
            SET preview_generation = 1,
                formal_preview_id = NULL,
                log_detection_status = 'not_evaluated',
                source_profile = NULL,
                detector_rule_version = NULL,
                detector_manifest_sha256 = NULL,
                detector_evidence_sha256 = NULL,
                preview_status = 'preview_generating',
                review_status = 'not_reviewed',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (asset["id"],),
        )
        conn.execute(
            """
            INSERT INTO jobs (
                job_type, status, asset_id, payload_json, dedup_key,
                preview_generation
            ) VALUES ('preview', 'queued', ?, ?, ?, 1)
            """,
            (
                asset["id"],
                json.dumps(
                    {
                        "asset_id": asset["id"],
                        "preview_generation": 1,
                        "type": "video",
                        "detection_required": True,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                dedup_key,
            ),
        )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _inject(injector: FaultInjector | None, step: str) -> None:
    if injector is not None:
        injector(step)
