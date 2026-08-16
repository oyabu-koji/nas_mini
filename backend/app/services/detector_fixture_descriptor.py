from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from app.services.detector_manifest import DetectorValidationError


LOCAL_DESCRIPTOR_SCHEMA_VERSION = 2
LOCAL_DESCRIPTOR_MAX_BYTES = 16_384
LOCAL_DESCRIPTOR_REPOSITORY_PATH = PurePosixPath(
    "data/detector-certification-v2.json"
)
LOCAL_DESCRIPTOR_FIELDS = frozenset({"schema_version", "fixtures"})
LOCAL_FIXTURE_FIELDS = frozenset(
    {
        "evidence_class",
        "expected_detection_status",
        "expected_sha256",
        "expected_source_profile",
        "path",
        "provenance",
        "role",
    }
)
FixtureRole = Literal["apple-log-2", "ordinary"]
FIXTURE_ROLES = frozenset({"apple-log-2", "ordinary"})
FixtureEvidenceClass = Literal["real-container"]
FIXTURE_EVIDENCE_CLASSES = frozenset({"real-container"})
LOCAL_FIXTURE_PROVENANCE = "user-owned-local-recording"
FixtureProvenance = Literal["user-owned-local-recording"]
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class LocalFixtureInput:
    evidence_class: FixtureEvidenceClass
    expected_detection_status: str
    expected_sha256: str
    expected_source_profile: str | None
    path: str
    provenance: FixtureProvenance
    role: FixtureRole


@dataclass(frozen=True)
class LocalFixtureDescriptor:
    fixtures: tuple[LocalFixtureInput, ...]


def validate_relative_fixture_path(value: object) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1_024
        or "\x00" in value
        or "\\" in value
        or value.startswith("/")
    ):
        raise DetectorValidationError()
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise DetectorValidationError()
    relative_path = PurePosixPath(value)
    if relative_path.is_absolute():
        raise DetectorValidationError()
    return relative_path


def confine_fixture_path(fixture_root: Path, value: object) -> Path:
    relative_path = validate_relative_fixture_path(value)
    candidate = fixture_root.joinpath(*relative_path.parts)
    try:
        resolved_root = fixture_root.resolve(strict=True)
        candidate.resolve(strict=True).relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise DetectorValidationError() from exc
    return candidate


def validate_fixture_root(fixture_root: Path) -> None:
    try:
        metadata = fixture_root.lstat()
    except OSError as exc:
        raise DetectorValidationError() from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or fixture_root.is_symlink()
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise DetectorValidationError()


def validate_descriptor_file(descriptor_path: Path) -> None:
    try:
        metadata = descriptor_path.lstat()
    except OSError as exc:
        raise DetectorValidationError() from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or descriptor_path.is_symlink()
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise DetectorValidationError()


def load_local_fixture_descriptor(descriptor_path: Path) -> LocalFixtureDescriptor:
    validate_descriptor_file(descriptor_path)
    try:
        with descriptor_path.open("rb") as source:
            raw = source.read(LOCAL_DESCRIPTOR_MAX_BYTES + 1)
    except OSError as exc:
        raise DetectorValidationError() from exc
    if not raw or len(raw) > LOCAL_DESCRIPTOR_MAX_BYTES:
        raise DetectorValidationError()
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise DetectorValidationError() from exc
    if (
        not isinstance(value, dict)
        or set(value) != LOCAL_DESCRIPTOR_FIELDS
        or value["schema_version"] != LOCAL_DESCRIPTOR_SCHEMA_VERSION
        or not isinstance(value["fixtures"], list)
        or len(value["fixtures"]) != 2
    ):
        raise DetectorValidationError()

    parsed: list[LocalFixtureInput] = []
    roles: set[str] = set()
    expected_by_role = {
        "apple-log-2": ("apple_log", "apple-log-2"),
        "ordinary": ("not_log", None),
    }
    for item in value["fixtures"]:
        if not isinstance(item, dict) or set(item) != LOCAL_FIXTURE_FIELDS:
            raise DetectorValidationError()
        role = item["role"]
        if role not in FIXTURE_ROLES or role in roles:
            raise DetectorValidationError()
        roles.add(role)
        expected_status, expected_profile = expected_by_role[role]
        if (
            item["evidence_class"] not in FIXTURE_EVIDENCE_CLASSES
            or item["expected_detection_status"] != expected_status
            or item["expected_source_profile"] != expected_profile
            or item["provenance"] != LOCAL_FIXTURE_PROVENANCE
            or not isinstance(item["expected_sha256"], str)
            or SHA256_PATTERN.fullmatch(item["expected_sha256"]) is None
        ):
            raise DetectorValidationError()
        relative_path = validate_relative_fixture_path(item["path"])
        parsed.append(
            LocalFixtureInput(
                evidence_class=item["evidence_class"],
                expected_detection_status=expected_status,
                expected_sha256=item["expected_sha256"],
                expected_source_profile=expected_profile,
                path=relative_path.as_posix(),
                provenance=LOCAL_FIXTURE_PROVENANCE,
                role=role,
            )
        )
    if roles != FIXTURE_ROLES or tuple(item.role for item in parsed) != (
        "apple-log-2",
        "ordinary",
    ):
        raise DetectorValidationError()
    return LocalFixtureDescriptor(fixtures=tuple(parsed))


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DetectorValidationError()
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise DetectorValidationError()
