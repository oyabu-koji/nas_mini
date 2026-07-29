from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.core.settings import Settings
from app.db.connection import connect
from app.db.phase_schema_identity import resolve_managed_phase_schema
from app.services.phase2_rollout import resolve_phase2_rollout
from app.services.safe_delete_candidate import (
    SAFE_TO_DELETE_CANDIDATE,
    evaluate_safe_delete_candidate,
    project_candidate_status,
)


@dataclass(frozen=True)
class ReconciliationSummary:
    status: str
    examined: int
    promoted: int
    demoted: int
    unchanged: int
    reasons: dict[str, int]


def reconcile_safe_delete_candidates(
    *,
    settings: Settings,
    apply_changes: bool,
) -> ReconciliationSummary:
    rollout = resolve_phase2_rollout(settings=settings)
    if not rollout.phase2c_schema_enabled:
        raise RuntimeError("phase2c_migration_schema_identity_mismatch")

    promoted = 0
    demoted = 0
    unchanged = 0
    reasons: Counter[str] = Counter()
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            locked_schema = resolve_managed_phase_schema(conn)
            if not locked_schema.phase2c_valid:
                raise RuntimeError("phase2c_migration_schema_identity_mismatch")
            asset_ids = [
                int(row["id"])
                for row in conn.execute(
                    """
                    SELECT id FROM assets
                    WHERE review_status = 'preview_confirmed'
                       OR delete_candidate_status = 'safe_to_delete_candidate'
                    ORDER BY id
                    """
                )
            ]
            for asset_id in asset_ids:
                before = conn.execute(
                    "SELECT delete_candidate_status FROM assets WHERE id = ?",
                    (asset_id,),
                ).fetchone()["delete_candidate_status"]
                evaluation = evaluate_safe_delete_candidate(
                    conn,
                    asset_id=asset_id,
                )
                after = project_candidate_status(
                    conn,
                    asset_id=asset_id,
                    evaluation=evaluation,
                    allow_promotion=rollout.safe_delete_candidate,
                )
                if before != after and after == SAFE_TO_DELETE_CANDIDATE:
                    promoted += 1
                elif before != after:
                    demoted += 1
                else:
                    unchanged += 1
                if evaluation.reason is not None:
                    reasons[evaluation.reason] += 1
            if apply_changes:
                conn.commit()
                status = "applied"
            else:
                conn.rollback()
                status = "dry_run"
        except Exception:
            conn.rollback()
            raise
    return ReconciliationSummary(
        status=status,
        examined=len(asset_ids),
        promoted=promoted,
        demoted=demoted,
        unchanged=unchanged,
        reasons=dict(reasons),
    )
