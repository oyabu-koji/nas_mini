from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.settings import Settings
from app.db.connection import connect
from app.db.phase2b import has_valid_phase2b_schema


CLIENT_VERSION_HEADER = "X-MediaVault-Client-Version"
MINIMUM_FORMAL_PREVIEW_CLIENT_VERSION = "0.2.0"
SEMANTIC_VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class IncompatibleClientError(RuntimeError):
    code = "incompatible_client"


@dataclass(frozen=True, order=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int


def parse_semantic_version(value: str) -> SemanticVersion:
    if not isinstance(value, str):
        raise ValueError("semantic version must be a string")
    match = SEMANTIC_VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("semantic version must contain three numeric components")
    return SemanticVersion(*(int(component) for component in match.groups()))


def require_compatible_client_for_asset(
    *,
    settings: Settings,
    asset_id: int,
    client_version: str | None,
) -> bool:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        if not has_valid_phase2b_schema(conn):
            return False
        row = conn.execute(
            """
            SELECT assets.preview_generation
            FROM assets
            JOIN upload_sessions
              ON upload_sessions.asset_id = assets.id
             AND upload_sessions.type = 'video'
            WHERE assets.id = ?
              AND assets.type = 'video'
              AND assets.preview_generation >= 1
            """,
            (asset_id,),
        ).fetchone()
    if row is None:
        return False
    try:
        supplied = parse_semantic_version(client_version or "")
        minimum = parse_semantic_version(MINIMUM_FORMAL_PREVIEW_CLIENT_VERSION)
    except ValueError as exc:
        raise IncompatibleClientError() from exc
    if supplied < minimum:
        raise IncompatibleClientError()
    return True
