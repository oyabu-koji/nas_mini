from __future__ import annotations

from dataclasses import dataclass

from app.core.settings import Settings
from app.db.connection import connect
from app.db.phase_schema_identity import resolve_managed_phase_schema
from app.services.client_compatibility import (
    IncompatibleClientError,
    parse_semantic_version,
)
from app.services.detector_capability import evaluate_detector_runtime


@dataclass(frozen=True)
class Phase2RolloutSnapshot:
    phase2b_schema_enabled: bool
    phase2c_schema_enabled: bool
    minimum_client_version: str | None
    phase2_asset: bool
    detector_certified: bool
    formal_apple_log_preview: bool
    safe_delete_candidate: bool
    runtime_blocked_reason: str | None
    detector_v2_schema_enabled: bool = False


def resolve_phase2_rollout(
    *,
    settings: Settings,
    asset_id: int | None = None,
    client_version: str | None = None,
    require_client_for_phase2_asset: bool = False,
) -> Phase2RolloutSnapshot:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        schema = resolve_managed_phase_schema(conn)
        phase2_asset = (
            _is_session_video(conn, asset_id=asset_id)
            if asset_id is not None and schema.phase2b_valid
            else False
        )
    if (
        require_client_for_phase2_asset
        and phase2_asset
        and schema.minimum_client_version is not None
    ):
        _require_version(
            supplied=client_version,
            minimum=schema.minimum_client_version,
        )
        from app.services.initial_release_guard import (
            assert_generated_apple_log_conversion_disabled,
        )

        assert_generated_apple_log_conversion_disabled(settings)

    runtime = evaluate_detector_runtime(settings)
    formal_enabled = bool(
        schema.detector_v2_valid
        and runtime.detector_certified
        and runtime.formal_apple_log_preview
    )
    return Phase2RolloutSnapshot(
        phase2b_schema_enabled=schema.phase2b_valid,
        phase2c_schema_enabled=schema.phase2c_valid,
        minimum_client_version=schema.minimum_client_version,
        phase2_asset=phase2_asset,
        detector_certified=runtime.detector_certified,
        formal_apple_log_preview=formal_enabled,
        safe_delete_candidate=bool(schema.phase2c_valid and formal_enabled),
        runtime_blocked_reason=runtime.blocked_reason,
        detector_v2_schema_enabled=schema.detector_v2_valid,
    )


def _is_session_video(conn, *, asset_id: int) -> bool:
    return (
        conn.execute(
            """
            SELECT 1
            FROM assets
            JOIN upload_sessions ON upload_sessions.asset_id = assets.id
            WHERE assets.id = ?
              AND assets.type = 'video'
              AND upload_sessions.type = 'video'
            LIMIT 1
            """,
            (asset_id,),
        ).fetchone()
        is not None
    )


def _require_version(*, supplied: str | None, minimum: str) -> None:
    try:
        supplied_version = parse_semantic_version(supplied or "")
        minimum_version = parse_semantic_version(minimum)
    except ValueError as exc:
        raise IncompatibleClientError() from exc
    if supplied_version < minimum_version:
        raise IncompatibleClientError()
