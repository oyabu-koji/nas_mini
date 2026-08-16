from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.services.canonical_json import CanonicalJsonError, canonical_json_bytes, sha256_hex
from app.services.iso_bmff_log_parser import (
    MAX_BOX_HEADERS,
    MAX_FILE_SIZE,
    MAX_METADATA_BYTES,
    MAX_NESTING_DEPTH,
    MAX_RETAINED_IDENTIFIERS,
    MAX_SAMPLE_DESCRIPTIONS,
    MAX_VIDEO_TRACKS,
)


DETECTOR_ID = "apple-log-v2"
RULE_SCHEMA_VERSION = 2
MANIFEST_SCHEMA_VERSION = 2
PARSER_CONTRACT_VERSION = "iso-bmff-apple-log-v1"
OFFICIAL_IDENTIFIER_SOURCE_URL = (
    "https://developer.apple.com/documentation/videotoolbox/"
    "kvtcompressionpropertykey_logtransferfunction"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TAG_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
TOP_LEVEL_RULE_FIELDS = frozenset(
    {
        "schema_version",
        "detector_id",
        "rule_version",
        "parser_contract_version",
        "identifier_mappings",
        "profile_preset_mappings",
        "color_allowlists",
        "not_log_predicate",
        "resource_limits",
        "official_source_url",
        "approval",
    }
)
IDENTIFIER_MAPPING_FIELDS = frozenset(
    {
        "identifier",
        "source_profile",
        "signal_kind",
        "rationale",
        "source_reference",
    }
)
PROFILE_PRESET_MAPPING_FIELDS = frozenset(
    {"source_profile", "requested_preset_id"}
)
COLOR_ALLOWLIST_FIELDS = frozenset(
    {"source_profile", "color_primaries", "color_transfer", "color_space"}
)
NOT_LOG_PREDICATE_FIELDS = frozenset(
    {"color_primaries", "color_transfer", "color_space"}
)
RESOURCE_LIMIT_FIELDS = frozenset(
    {
        "file_size_bytes",
        "box_headers",
        "nesting_depth",
        "video_tracks",
        "sample_descriptions",
        "metadata_bytes",
        "retained_identifiers",
    }
)
SOURCE_PROFILES = frozenset({"apple-log-1", "apple-log-2"})
SIGNAL_KINDS = frozenset({"apple-log-1-logs", "apple-log-2-logs"})
EXPECTED_IDENTIFIER_MAPPINGS = (
    (
        "com.apple.rec2020.apple-log",
        "apple-log-1",
        "apple-log-1-logs",
    ),
    (
        "com.apple.apple-wide-gamut.apple-log",
        "apple-log-2",
        "apple-log-2-logs",
    ),
)
EXPECTED_PROFILE_PRESET_MAPPINGS = (
    ("apple-log-1", "generated-apple-log-rec709"),
    ("apple-log-2", "generated-apple-log2-rec709"),
)
EXPECTED_PROFILE_COLOR_ALLOWLISTS = (
    (
        "apple-log-1",
        (None, "unknown", "bt2020"),
        (None, "unknown"),
        (None, "unknown", "bt2020nc"),
    ),
    (
        "apple-log-2",
        (None, "unknown"),
        (None, "unknown"),
        (None, "unknown", "bt2020nc"),
    ),
)
PARSER_RESOURCE_LIMITS = {
    "file_size_bytes": MAX_FILE_SIZE,
    "box_headers": MAX_BOX_HEADERS,
    "nesting_depth": MAX_NESTING_DEPTH,
    "video_tracks": MAX_VIDEO_TRACKS,
    "sample_descriptions": MAX_SAMPLE_DESCRIPTIONS,
    "metadata_bytes": MAX_METADATA_BYTES,
    "retained_identifiers": MAX_RETAINED_IDENTIFIERS,
}
EXPECTED_NOT_LOG_PREDICATE = ("bt709", "bt709", "bt709")
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
        "parser_contract_version",
        "identifier_mappings",
        "profile_preset_mappings",
        "color_allowlists",
        "not_log_predicate",
        "resource_limits",
        "official_source_url",
        "ffprobe_version",
        "show_entries",
        "timeout_ms",
        "max_stdout_bytes",
        "max_stderr_bytes",
        "max_evidence_bytes",
        "fixtures",
        "manifest_sha256",
    }
)
FIXTURE_FIELDS = frozenset(
    {
        "role",
        "evidence_class",
        "sha256",
        "expected_detection_status",
        "expected_source_profile",
        "provenance",
    }
)
SUMMARY_FIELDS = frozenset(
    {
        "schema_version",
        "detector_id",
        "manifest_sha256",
        "rule_input_sha256",
        "parser_contract_version",
        "ffprobe_version",
        "future_apple_log_1_transform_allowed",
        "fixtures",
    }
)
SUMMARY_FIXTURE_FIELDS = frozenset(
    {
        "role",
        "evidence_class",
        "sha256",
        "expected_detection_status",
        "expected_source_profile",
    }
)
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
    "stream=index,id,codec_type,color_space,color_transfer,color_primaries"
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
    parser_contract_version: str
    identifier_mappings: tuple["IdentifierMapping", ...]
    profile_preset_mappings: tuple["ProfilePresetMapping", ...]
    color_allowlists: tuple["ProfileColorAllowlist", ...]
    not_log_predicate: "NotLogPredicate"
    resource_limits: tuple[tuple[str, int], ...]
    official_source_url: str
    canonical_bytes: bytes
    sha256: str


@dataclass(frozen=True)
class IdentifierMapping:
    identifier: str
    source_profile: str
    signal_kind: str
    rationale: str
    source_reference: str


@dataclass(frozen=True)
class ProfilePresetMapping:
    source_profile: str
    requested_preset_id: str


@dataclass(frozen=True)
class ProfileColorAllowlist:
    source_profile: str
    color_primaries: tuple[str | None, ...]
    color_transfer: tuple[str | None, ...]
    color_space: tuple[str | None, ...]


@dataclass(frozen=True)
class NotLogPredicate:
    color_primaries: str
    color_transfer: str
    color_space: str


@dataclass(frozen=True)
class DetectorManifest:
    detector_id: str
    rule_version: str
    rule_input_sha256: str
    parser_contract_version: str
    identifier_mappings: tuple[IdentifierMapping, ...]
    profile_preset_mappings: tuple[ProfilePresetMapping, ...]
    color_allowlists: tuple[ProfileColorAllowlist, ...]
    not_log_predicate: NotLogPredicate
    resource_limits: tuple[tuple[str, int], ...]
    official_source_url: str
    ffprobe_version: str
    show_entries: str
    timeout_ms: int
    max_stdout_bytes: int
    max_stderr_bytes: int
    max_evidence_bytes: int
    fixtures: tuple["ManifestFixture", ...]
    manifest_sha256: str
    canonical_bytes: bytes


@dataclass(frozen=True)
class ManifestFixture:
    role: str
    evidence_class: str
    sha256: str
    expected_detection_status: str
    expected_source_profile: str | None
    provenance: str


@dataclass(frozen=True)
class CertificateSummary:
    manifest_sha256: str
    rule_input_sha256: str
    parser_contract_version: str
    ffprobe_version: str
    future_apple_log_1_transform_allowed: bool
    fixtures: tuple["SummaryFixture", ...]


@dataclass(frozen=True)
class SummaryFixture:
    role: str
    evidence_class: str
    sha256: str
    expected_detection_status: str
    expected_source_profile: str | None


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
    canonical = _canonical_bytes(value)
    if set(value) != TOP_LEVEL_RULE_FIELDS:
        raise DetectorValidationError()
    if value["schema_version"] != RULE_SCHEMA_VERSION or value["detector_id"] != DETECTOR_ID:
        raise DetectorValidationError()
    rule_version = _bounded_identifier(value["rule_version"])
    parser_contract_version = _bounded_identifier(value["parser_contract_version"])
    if parser_contract_version != PARSER_CONTRACT_VERSION:
        raise DetectorValidationError()
    identifier_mappings = _parse_identifier_mappings(value["identifier_mappings"])
    if tuple(
        (item.identifier, item.source_profile, item.signal_kind)
        for item in identifier_mappings
    ) != EXPECTED_IDENTIFIER_MAPPINGS:
        raise DetectorValidationError()
    profile_preset_mappings = _parse_profile_preset_mappings(
        value["profile_preset_mappings"]
    )
    if tuple(
        (item.source_profile, item.requested_preset_id)
        for item in profile_preset_mappings
    ) != EXPECTED_PROFILE_PRESET_MAPPINGS:
        raise DetectorValidationError()
    color_allowlists = _parse_color_allowlists(value["color_allowlists"])
    if tuple(
        (
            item.source_profile,
            item.color_primaries,
            item.color_transfer,
            item.color_space,
        )
        for item in color_allowlists
    ) != EXPECTED_PROFILE_COLOR_ALLOWLISTS:
        raise DetectorValidationError()
    not_log_predicate = _parse_not_log_predicate(value["not_log_predicate"])
    if (
        not_log_predicate.color_primaries,
        not_log_predicate.color_transfer,
        not_log_predicate.color_space,
    ) != EXPECTED_NOT_LOG_PREDICATE:
        raise DetectorValidationError()
    resource_limits = _parse_resource_limits(value["resource_limits"])
    if dict(resource_limits) != PARSER_RESOURCE_LIMITS:
        raise DetectorValidationError()
    official_source_url = _validate_https_url(value["official_source_url"])
    if official_source_url != OFFICIAL_IDENTIFIER_SOURCE_URL or any(
        item.source_reference != OFFICIAL_IDENTIFIER_SOURCE_URL
        for item in identifier_mappings
    ):
        raise DetectorValidationError()
    _validate_approval(value["approval"])
    digest = sha256_hex(canonical)
    sidecar = path.parent / f"{path.name}.sha256"
    try:
        recorded = sidecar.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise DetectorValidationError() from exc
    if not SHA256_PATTERN.fullmatch(recorded) or recorded != digest:
        raise DetectorValidationError()
    return RuleInput(
        detector_id=DETECTOR_ID,
        rule_version=rule_version,
        parser_contract_version=parser_contract_version,
        identifier_mappings=identifier_mappings,
        profile_preset_mappings=profile_preset_mappings,
        color_allowlists=color_allowlists,
        not_log_predicate=not_log_predicate,
        resource_limits=resource_limits,
        official_source_url=official_source_url,
        canonical_bytes=canonical,
        sha256=digest,
    )


def load_detector_manifest(path: Path, *, rule_input: RuleInput) -> DetectorManifest:
    raw = _read_bounded(path, 65_536)
    value = _load_canonical_object(raw)
    canonical = _canonical_bytes(value)
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
        or value["parser_contract_version"] != PARSER_CONTRACT_VERSION
        or value["show_entries"] != FFPROBE_SHOW_ENTRIES
    ):
        raise DetectorValidationError()
    approved_value = _load_canonical_object(rule_input.canonical_bytes)
    copied_fields = (
        "parser_contract_version",
        "identifier_mappings",
        "profile_preset_mappings",
        "color_allowlists",
        "not_log_predicate",
        "resource_limits",
        "official_source_url",
    )
    if any(
        _canonical_bytes(value[field]) != _canonical_bytes(approved_value[field])
        for field in copied_fields
    ):
        raise DetectorValidationError()
    identifier_mappings = _parse_identifier_mappings(value["identifier_mappings"])
    profile_preset_mappings = _parse_profile_preset_mappings(
        value["profile_preset_mappings"]
    )
    color_allowlists = _parse_color_allowlists(value["color_allowlists"])
    not_log_predicate = _parse_not_log_predicate(value["not_log_predicate"])
    resource_limits = _parse_resource_limits(value["resource_limits"])
    official_source_url = _validate_https_url(value["official_source_url"])
    fixtures = _validate_fixtures(value["fixtures"])
    ffprobe_version = _bounded_text(value["ffprobe_version"], 256)
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
        parser_contract_version=PARSER_CONTRACT_VERSION,
        identifier_mappings=identifier_mappings,
        profile_preset_mappings=profile_preset_mappings,
        color_allowlists=color_allowlists,
        not_log_predicate=not_log_predicate,
        resource_limits=resource_limits,
        official_source_url=official_source_url,
        ffprobe_version=ffprobe_version,
        show_entries=FFPROBE_SHOW_ENTRIES,
        timeout_ms=DETECTOR_PROBE_TIMEOUT_MS,
        max_stdout_bytes=DETECTOR_MAX_STDOUT_BYTES,
        max_stderr_bytes=DETECTOR_MAX_STDERR_BYTES,
        max_evidence_bytes=DETECTOR_MAX_EVIDENCE_BYTES,
        fixtures=fixtures,
        manifest_sha256=digest,
        canonical_bytes=canonical,
    )


def load_certificate_summary(
    path: Path, *, rule_input: RuleInput, manifest: DetectorManifest
) -> CertificateSummary:
    raw = _read_bounded(path, 16_384)
    value = _load_canonical_object(raw)
    if (
        set(value) != SUMMARY_FIELDS
        or value["schema_version"] != 2
        or value["detector_id"] != DETECTOR_ID
        or value["parser_contract_version"] != PARSER_CONTRACT_VERSION
        or value["future_apple_log_1_transform_allowed"] is not False
    ):
        raise DetectorValidationError()
    if (
        value["manifest_sha256"] != manifest.manifest_sha256
        or value["rule_input_sha256"] != rule_input.sha256
        or value["ffprobe_version"] != manifest.ffprobe_version
    ):
        raise DetectorValidationError()
    fixtures = value["fixtures"]
    if not isinstance(fixtures, list) or len(fixtures) != len(manifest.fixtures):
        raise DetectorValidationError()
    parsed: list[SummaryFixture] = []
    for fixture, manifest_fixture in zip(fixtures, manifest.fixtures, strict=True):
        if not isinstance(fixture, dict) or set(fixture) != SUMMARY_FIXTURE_FIELDS:
            raise DetectorValidationError()
        if (
            fixture["role"] != manifest_fixture.role
            or fixture["evidence_class"] != manifest_fixture.evidence_class
            or fixture["sha256"] != manifest_fixture.sha256
            or fixture["expected_detection_status"]
            != manifest_fixture.expected_detection_status
            or fixture["expected_source_profile"]
            != manifest_fixture.expected_source_profile
            or not SHA256_PATTERN.fullmatch(str(fixture["sha256"]))
        ):
            raise DetectorValidationError()
        parsed.append(
            SummaryFixture(
                role=manifest_fixture.role,
                evidence_class=manifest_fixture.evidence_class,
                sha256=manifest_fixture.sha256,
                expected_detection_status=manifest_fixture.expected_detection_status,
                expected_source_profile=manifest_fixture.expected_source_profile,
            )
        )
    return CertificateSummary(
        manifest_sha256=manifest.manifest_sha256,
        rule_input_sha256=rule_input.sha256,
        parser_contract_version=PARSER_CONTRACT_VERSION,
        ffprobe_version=manifest.ffprobe_version,
        future_apple_log_1_transform_allowed=False,
        fixtures=tuple(parsed),
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
    canonical = _canonical_bytes(value)
    if not isinstance(value, dict) or raw not in {canonical, canonical + b"\n"}:
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


def _parse_identifier_mappings(value: Any) -> tuple[IdentifierMapping, ...]:
    if not isinstance(value, list) or len(value) != 2:
        raise DetectorValidationError()
    parsed: list[IdentifierMapping] = []
    identifiers: set[str] = set()
    profiles: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != IDENTIFIER_MAPPING_FIELDS:
            raise DetectorValidationError()
        identifier = _bounded_ascii_text(item["identifier"], 128)
        source_profile = item["source_profile"]
        signal_kind = item["signal_kind"]
        if (
            source_profile not in SOURCE_PROFILES
            or signal_kind not in SIGNAL_KINDS
            or identifier in identifiers
            or source_profile in profiles
        ):
            raise DetectorValidationError()
        identifiers.add(identifier)
        profiles.add(source_profile)
        parsed.append(
            IdentifierMapping(
                identifier=identifier,
                source_profile=source_profile,
                signal_kind=signal_kind,
                rationale=_bounded_text(item["rationale"], 512),
                source_reference=_validate_https_url(item["source_reference"]),
            )
        )
    if profiles != SOURCE_PROFILES:
        raise DetectorValidationError()
    return tuple(parsed)


def _parse_profile_preset_mappings(value: Any) -> tuple[ProfilePresetMapping, ...]:
    if not isinstance(value, list) or len(value) != 2:
        raise DetectorValidationError()
    parsed: list[ProfilePresetMapping] = []
    profiles: set[str] = set()
    preset_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != PROFILE_PRESET_MAPPING_FIELDS:
            raise DetectorValidationError()
        source_profile = item["source_profile"]
        preset_id = _bounded_identifier(item["requested_preset_id"])
        if (
            source_profile not in SOURCE_PROFILES
            or source_profile in profiles
            or preset_id in preset_ids
        ):
            raise DetectorValidationError()
        profiles.add(source_profile)
        preset_ids.add(preset_id)
        parsed.append(
            ProfilePresetMapping(
                source_profile=source_profile,
                requested_preset_id=preset_id,
            )
        )
    if profiles != SOURCE_PROFILES:
        raise DetectorValidationError()
    return tuple(parsed)


def _parse_color_allowlists(value: Any) -> tuple[ProfileColorAllowlist, ...]:
    if not isinstance(value, list) or len(value) != 2:
        raise DetectorValidationError()
    parsed: list[ProfileColorAllowlist] = []
    profiles: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != COLOR_ALLOWLIST_FIELDS:
            raise DetectorValidationError()
        source_profile = item["source_profile"]
        if source_profile not in SOURCE_PROFILES or source_profile in profiles:
            raise DetectorValidationError()
        profiles.add(source_profile)
        parsed.append(
            ProfileColorAllowlist(
                source_profile=source_profile,
                color_primaries=_parse_nullable_allowlist(item["color_primaries"]),
                color_transfer=_parse_nullable_allowlist(item["color_transfer"]),
                color_space=_parse_nullable_allowlist(item["color_space"]),
            )
        )
    if profiles != SOURCE_PROFILES:
        raise DetectorValidationError()
    return tuple(parsed)


def _parse_nullable_allowlist(value: Any) -> tuple[str | None, ...]:
    if not isinstance(value, list) or not value or len(value) > 8:
        raise DetectorValidationError()
    parsed: list[str | None] = []
    for item in value:
        if item is not None:
            item = _bounded_identifier(item)
        if item in parsed:
            raise DetectorValidationError()
        parsed.append(item)
    return tuple(parsed)


def _parse_not_log_predicate(value: Any) -> NotLogPredicate:
    if not isinstance(value, dict) or set(value) != NOT_LOG_PREDICATE_FIELDS:
        raise DetectorValidationError()
    return NotLogPredicate(
        color_primaries=_bounded_identifier(value["color_primaries"]),
        color_transfer=_bounded_identifier(value["color_transfer"]),
        color_space=_bounded_identifier(value["color_space"]),
    )


def _parse_resource_limits(value: Any) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, dict) or set(value) != RESOURCE_LIMIT_FIELDS:
        raise DetectorValidationError()
    parsed: list[tuple[str, int]] = []
    for name in sorted(RESOURCE_LIMIT_FIELDS):
        limit = value[name]
        if type(limit) is not int or limit <= 0:
            raise DetectorValidationError()
        parsed.append((name, limit))
    return tuple(parsed)


def _validate_https_url(value: Any) -> str:
    text = _bounded_text(value, 512)
    parsed = urlsplit(text)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise DetectorValidationError()
    return text


def _bounded_ascii_text(value: Any, maximum: int) -> str:
    text = _bounded_text(value, maximum)
    try:
        text.encode("ascii", errors="strict")
    except UnicodeError as exc:
        raise DetectorValidationError() from exc
    if "\x00" in text:
        raise DetectorValidationError()
    return text


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


def _validate_fixtures(value: Any) -> tuple[ManifestFixture, ...]:
    if not isinstance(value, list) or len(value) != 3:
        raise DetectorValidationError()
    parsed: list[ManifestFixture] = []
    roles: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != FIXTURE_FIELDS:
            raise DetectorValidationError()
        role = item["role"]
        expected = {
            "apple-log-1": ("synthetic-container", "apple_log", "apple-log-1"),
            "apple-log-2": ("real-container", "apple_log", "apple-log-2"),
            "ordinary": ("real-container", "not_log", None),
        }
        if role not in expected or role in roles:
            raise DetectorValidationError()
        roles.add(role)
        if not SHA256_PATTERN.fullmatch(str(item["sha256"])):
            raise DetectorValidationError()
        if (
            item["evidence_class"],
            item["expected_detection_status"],
            item["expected_source_profile"],
        ) != expected[role]:
            raise DetectorValidationError()
        expected_provenance = (
            "project-owned-synthetic-container"
            if role == "apple-log-1"
            else "user-owned-local-recording"
        )
        if item["provenance"] != expected_provenance:
            raise DetectorValidationError()
        parsed.append(
            ManifestFixture(
                role=role,
                evidence_class=item["evidence_class"],
                sha256=item["sha256"],
                expected_detection_status=item["expected_detection_status"],
                expected_source_profile=item["expected_source_profile"],
                provenance=item["provenance"],
            )
        )
    if tuple(item.role for item in parsed) != (
        "apple-log-1",
        "apple-log-2",
        "ordinary",
    ):
        raise DetectorValidationError()
    return tuple(parsed)


def _bounded_identifier(value: Any) -> str:
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise DetectorValidationError()
    return value


def _bounded_text(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DetectorValidationError()
    return value
