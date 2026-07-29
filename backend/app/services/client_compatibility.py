from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.settings import Settings


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
    from app.services.phase2_rollout import resolve_phase2_rollout

    return resolve_phase2_rollout(
        settings=settings,
        asset_id=asset_id,
        client_version=client_version,
        require_client_for_phase2_asset=True,
    ).phase2_asset
