from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.canonical_json import CanonicalJsonError, canonical_json_bytes, sha256_hex


DETECTOR_ID = "apple-log-v1"
RULE_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TAG_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
TOP_LEVEL_RULE_FIELDS = frozenset(
    {"schema_version", "detector_id", "rule_version", "apple_log", "not_log", "approval"}
)
PREDICATE_FIELDS = frozenset(
    {"path", "operator", "expected_value", "rationale", "source_reference"}
)
APPROVAL_FIELDS = frozenset({"approving_role", "approved_at", "approval_reference"})
MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "detector_id",
        "rule_version",
        "rule_input_sha256",
        "rules",
        "ffprobe_version",
        "show_entries",
        "timeout_ms",
        "max_stdout_bytes",
        "max_stderr_bytes",
        "max_evidence_bytes",
        "fixtures",
        "source_reference",
        "manifest_sha256",
    }
)
RULES_FIELDS = frozenset({"apple_log", "not_log"})
FIXTURE_FIELDS = frozenset({"role", "sha256", "expected_classification", "source_label"})
SUMMARY_FIELDS = frozenset(
    {"schema_version", "manifest_sha256", "rule_input_sha256", "ffprobe_version", "fixtures"}
)
SUMMARY_FIXTURE_FIELDS = frozenset({"role", "sha256"})
FIXTURE_INPUT_FIELDS = frozenset({"schema_version", "fixtures"})
FIXTURE_INPUT_ENTRY_FIELDS = frozenset(
    {
        "role",
        "relative_media_path",
        "expected_media_sha256",
        "expected_classification",
        "source_label",
    }
)
FFPROBE_SHOW_ENTRIES = (
    "stream=index,codec_type,color_space,color_transfer,color_primaries:"
    "stream_tags:stream_disposition:format_tags"
)
DETECTOR_PROBE_TIMEOUT_MS = 15_000
DETECTOR_MAX_STDOUT_BYTES = 1_048_576
DETECTOR_MAX_STDERR_BYTES = 1_048_576
DETECTOR_MAX_EVIDENCE_BYTES = 4_096
FFPROBE_PROBE_ARGUMENTS = (
    "-v",
    "error",
    "-print_format",
    "json",
    "-select_streams",
    "v:0",
    "-show_entries",
    FFPROBE_SHOW_ENTRIES,
)


class DetectorValidationError(ValueError):
    def __init__(self, code: str = "log_detector_manifest_invalid"):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Predicate:
    path: str
    operator: str
    expected_value: Any | None
    rationale: str
    source_reference: str


@dataclass(frozen=True)
class RuleInput:
    detector_id: str
    rule_version: str
    apple_log: tuple[Predicate, ...]
    not_log: tuple[Predicate, ...]
    canonical_bytes: bytes
    sha256: str


@dataclass(frozen=True)
class DetectorManifest:
    detector_id: str
    rule_version: str
    rule_input_sha256: str
    apple_log: tuple[Predicate, ...]
    not_log: tuple[Predicate, ...]
    ffprobe_version: str
    show_entries: str
    timeout_ms: int
    max_stdout_bytes: int
    max_stderr_bytes: int
    max_evidence_bytes: int
    manifest_sha256: str
    canonical_bytes: bytes


@dataclass(frozen=True)
class CertificateSummary:
    manifest_sha256: str
    rule_input_sha256: str
    ffprobe_version: str


@dataclass(frozen=True)
class FixtureInput:
    role: str
    relative_media_path: str
    expected_media_sha256: str
    expected_classification: str
    source_label: str


@dataclass(frozen=True)
class FixtureDescriptor:
    fixtures: tuple[FixtureInput, ...]


def load_fixture_descriptor(path: Path) -> FixtureDescriptor:
    raw = _read_bounded(path, 16_384)
    value = _load_canonical_object(raw)
    if set(value) != FIXTURE_INPUT_FIELDS or value["schema_version"] != 1:
        raise DetectorValidationError()
    fixtures = value["fixtures"]
    if not isinstance(fixtures, list) or len(fixtures) != 2:
        raise DetectorValidationError()

    parsed: list[FixtureInput] = []
    roles: set[str] = set()
    for item in fixtures:
        if not isinstance(item, dict) or set(item) != FIXTURE_INPUT_ENTRY_FIELDS:
            raise DetectorValidationError()
        role = item["role"]
        if role not in {"apple_log", "ordinary"} or role in roles:
            raise DetectorValidationError()
        roles.add(role)
        expected = "apple_log" if role == "apple_log" else "not_log"
        if item["expected_classification"] != expected:
            raise DetectorValidationError()
        relative_path = _bounded_text(item["relative_media_path"], 1024)
        if (
            relative_path.startswith(("/", "\\"))
            or "\x00" in relative_path
            or any(part in {"", ".", ".."} for part in relative_path.replace("\\", "/").split("/"))
        ):
            raise DetectorValidationError()
        digest = item["expected_media_sha256"]
        if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
            raise DetectorValidationError()
        if item["source_label"] != "user-owned-local-recording":
            raise DetectorValidationError()
        parsed.append(
            FixtureInput(
                role=role,
                relative_media_path=relative_path,
                expected_media_sha256=digest,
                expected_classification=expected,
                source_label="user-owned-local-recording",
            )
        )
    if roles != {"apple_log", "ordinary"}:
        raise DetectorValidationError()
    return FixtureDescriptor(fixtures=tuple(parsed))


def load_rule_input(path: Path) -> RuleInput:
    raw = _read_bounded(path, 65_536)
    value = _load_canonical_object(raw)
    if set(value) != TOP_LEVEL_RULE_FIELDS:
        raise DetectorValidationError()
    if value["schema_version"] != RULE_SCHEMA_VERSION or value["detector_id"] != DETECTOR_ID:
        raise DetectorValidationError()
    rule_version = _bounded_identifier(value["rule_version"])
    apple_log = _parse_predicates(value["apple_log"])
    not_log = _parse_predicates(value["not_log"])
    _validate_approval(value["approval"])
    digest = sha256_hex(raw)
    sidecar = path.with_suffix(".sha256")
    try:
        recorded = sidecar.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise DetectorValidationError() from exc
    if not SHA256_PATTERN.fullmatch(recorded) or recorded != digest:
        raise DetectorValidationError()
    return RuleInput(
        detector_id=DETECTOR_ID,
        rule_version=rule_version,
        apple_log=apple_log,
        not_log=not_log,
        canonical_bytes=raw,
        sha256=digest,
    )


def load_detector_manifest(path: Path, *, rule_input: RuleInput) -> DetectorManifest:
    raw = _read_bounded(path, 65_536)
    value = _load_canonical_object(raw)
    if set(value) != MANIFEST_FIELDS:
        raise DetectorValidationError()
    digest_payload = {key: member for key, member in value.items() if key != "manifest_sha256"}
    digest = sha256_hex(_canonical_bytes(digest_payload))
    if value["manifest_sha256"] != digest or not SHA256_PATTERN.fullmatch(digest):
        raise DetectorValidationError()
    if (
        value["schema_version"] != MANIFEST_SCHEMA_VERSION
        or value["detector_id"] != DETECTOR_ID
        or value["rule_version"] != rule_input.rule_version
        or value["rule_input_sha256"] != rule_input.sha256
        or value["show_entries"] != FFPROBE_SHOW_ENTRIES
    ):
        raise DetectorValidationError()
    rules = value["rules"]
    if not isinstance(rules, dict) or set(rules) != RULES_FIELDS:
        raise DetectorValidationError()
    apple_log = _parse_predicates(rules["apple_log"])
    not_log = _parse_predicates(rules["not_log"])
    approved_value = _load_canonical_object(rule_input.canonical_bytes)
    approved_rules = {
        "apple_log": approved_value["apple_log"],
        "not_log": approved_value["not_log"],
    }
    if (
        apple_log != rule_input.apple_log
        or not_log != rule_input.not_log
        or _canonical_bytes(rules) != _canonical_bytes(approved_rules)
    ):
        raise DetectorValidationError()
    _validate_fixtures(value["fixtures"])
    ffprobe_version = _bounded_text(value["ffprobe_version"], 256)
    source_reference = _bounded_text(value["source_reference"], 512)
    if not source_reference:
        raise DetectorValidationError()
    limits = (
        ("timeout_ms", DETECTOR_PROBE_TIMEOUT_MS),
        ("max_stdout_bytes", DETECTOR_MAX_STDOUT_BYTES),
        ("max_stderr_bytes", DETECTOR_MAX_STDERR_BYTES),
        ("max_evidence_bytes", DETECTOR_MAX_EVIDENCE_BYTES),
    )
    for field, expected in limits:
        if value[field] != expected:
            raise DetectorValidationError()
    return DetectorManifest(
        detector_id=DETECTOR_ID,
        rule_version=rule_input.rule_version,
        rule_input_sha256=rule_input.sha256,
        apple_log=apple_log,
        not_log=not_log,
        ffprobe_version=ffprobe_version,
        show_entries=FFPROBE_SHOW_ENTRIES,
        timeout_ms=DETECTOR_PROBE_TIMEOUT_MS,
        max_stdout_bytes=DETECTOR_MAX_STDOUT_BYTES,
        max_stderr_bytes=DETECTOR_MAX_STDERR_BYTES,
        max_evidence_bytes=DETECTOR_MAX_EVIDENCE_BYTES,
        manifest_sha256=digest,
        canonical_bytes=raw,
    )


def load_certificate_summary(
    path: Path, *, rule_input: RuleInput, manifest: DetectorManifest
) -> CertificateSummary:
    raw = _read_bounded(path, 16_384)
    value = _load_canonical_object(raw)
    if set(value) != SUMMARY_FIELDS or value["schema_version"] != 1:
        raise DetectorValidationError()
    if (
        value["manifest_sha256"] != manifest.manifest_sha256
        or value["rule_input_sha256"] != rule_input.sha256
        or value["ffprobe_version"] != manifest.ffprobe_version
    ):
        raise DetectorValidationError()
    fixtures = value["fixtures"]
    if not isinstance(fixtures, list) or len(fixtures) != 2:
        raise DetectorValidationError()
    roles = set()
    for fixture in fixtures:
        if not isinstance(fixture, dict) or set(fixture) != SUMMARY_FIXTURE_FIELDS:
            raise DetectorValidationError()
        if fixture["role"] not in {"apple_log", "ordinary"}:
            raise DetectorValidationError()
        if not SHA256_PATTERN.fullmatch(str(fixture["sha256"])):
            raise DetectorValidationError()
        roles.add(fixture["role"])
    if roles != {"apple_log", "ordinary"}:
        raise DetectorValidationError()
    return CertificateSummary(
        manifest_sha256=manifest.manifest_sha256,
        rule_input_sha256=rule_input.sha256,
        ffprobe_version=manifest.ffprobe_version,
    )


def canonical_document(value: dict[str, Any]) -> bytes:
    return _canonical_bytes(value)


def document_with_digest(value: dict[str, Any], digest_field: str) -> bytes:
    payload = {key: member for key, member in value.items() if key != digest_field}
    digest = sha256_hex(_canonical_bytes(payload))
    return _canonical_bytes({**payload, digest_field: digest})


def _read_bounded(path: Path, limit: int) -> bytes:
    try:
        with path.open("rb") as source:
            raw = source.read(limit + 1)
    except OSError as exc:
        raise DetectorValidationError() from exc
    if not raw or len(raw) > limit or raw.startswith(b"\xef\xbb\xbf"):
        raise DetectorValidationError()
    return raw


def _load_canonical_object(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError, DetectorValidationError) as exc:
        raise DetectorValidationError() from exc
    if not isinstance(value, dict) or _canonical_bytes(value) != raw:
        raise DetectorValidationError()
    return value


def _canonical_bytes(value: Any) -> bytes:
    try:
        return canonical_json_bytes(value)
    except CanonicalJsonError as exc:
        raise DetectorValidationError() from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DetectorValidationError()
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise DetectorValidationError()


def _parse_predicates(value: Any) -> tuple[Predicate, ...]:
    if not isinstance(value, list) or not value or len(value) > 32:
        raise DetectorValidationError()
    predicates: list[Predicate] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != PREDICATE_FIELDS:
            raise DetectorValidationError()
        path = _validate_path(item["path"])
        operator = item["operator"]
        if operator not in {"equals", "present"}:
            raise DetectorValidationError()
        expected_value = item["expected_value"]
        if (operator == "equals" and expected_value is None) or (
            operator == "present" and expected_value is not None
        ):
            raise DetectorValidationError()
        if isinstance(expected_value, (dict, list)) or not isinstance(
            expected_value, (str, int, float, bool, type(None))
        ):
            raise DetectorValidationError()
        predicates.append(
            Predicate(
                path=path,
                operator=operator,
                expected_value=expected_value,
                rationale=_bounded_text(item["rationale"], 512),
                source_reference=_bounded_text(item["source_reference"], 512),
            )
        )
    return tuple(predicates)


def _validate_path(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 160:
        raise DetectorValidationError()
    parts = value.split(".")
    if value.startswith("format.tags."):
        tag = value.removeprefix("format.tags.")
    elif value.startswith("streams.0.tags."):
        tag = value.removeprefix("streams.0.tags.")
    elif value.startswith("streams.0.disposition."):
        tag = value.removeprefix("streams.0.disposition.")
    elif parts[:2] == ["streams", "0"] and len(parts) == 3 and parts[2] in {
        "index",
        "codec_type",
        "color_space",
        "color_transfer",
        "color_primaries",
    }:
        return value
    else:
        raise DetectorValidationError()
    if not TAG_COMPONENT_PATTERN.fullmatch(tag):
        raise DetectorValidationError()
    return value


def _validate_approval(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != APPROVAL_FIELDS:
        raise DetectorValidationError()
    _bounded_text(value["approving_role"], 128)
    _bounded_text(value["approval_reference"], 512)
    approved_at = _bounded_text(value["approved_at"], 64)
    try:
        parsed = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DetectorValidationError() from exc
    if parsed.tzinfo is None:
        raise DetectorValidationError()


def _validate_fixtures(value: Any) -> None:
    if not isinstance(value, list) or len(value) != 2:
        raise DetectorValidationError()
    roles = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != FIXTURE_FIELDS:
            raise DetectorValidationError()
        if item["role"] not in {"apple_log", "ordinary"}:
            raise DetectorValidationError()
        roles.add(item["role"])
        if not SHA256_PATTERN.fullmatch(str(item["sha256"])):
            raise DetectorValidationError()
        expected = "apple_log" if item["role"] == "apple_log" else "not_log"
        if item["expected_classification"] != expected:
            raise DetectorValidationError()
        if item["source_label"] != "user-owned-local-recording":
            raise DetectorValidationError()
    if roles != {"apple_log", "ordinary"}:
        raise DetectorValidationError()


def _bounded_identifier(value: Any) -> str:
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise DetectorValidationError()
    return value


def _bounded_text(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DetectorValidationError()
    return value
