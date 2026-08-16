from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.services.bounded_subprocess import BoundedProcessError, run_bounded_process
from app.services.detector_manifest import (
    DetectorManifest,
    DetectorValidationError,
    FFPROBE_PROBE_ARGUMENTS,
    Predicate,
    canonical_document,
)
from app.services.iso_bmff_log_parser import ContainerSignal


@dataclass(frozen=True)
class DetectionResult:
    status: str
    source_profile: str | None
    evidence_sha256: str
    evidence_json: bytes


TrackIdStatus = Literal["valid", "unresolved"]
STREAM_ID_PATTERN = re.compile(r"^0x[0-9a-f]+$")
PROBE_TOP_LEVEL_FIELDS = frozenset({"streams", "programs", "stream_groups"})
PROBE_STREAM_FIELDS = frozenset(
    {
        "index",
        "id",
        "codec_type",
        "color_space",
        "color_transfer",
        "color_primaries",
        "side_data_list",
    }
)


@dataclass(frozen=True)
class ProbeSignal:
    index: int
    track_id_status: TrackIdStatus
    track_id: int | None
    codec_type: str | None
    color_space: str | None
    color_transfer: str | None
    color_primaries: str | None


def parse_stream_id(value: Any) -> tuple[TrackIdStatus, int | None]:
    if not isinstance(value, str) or STREAM_ID_PATTERN.fullmatch(value) is None:
        return "unresolved", None
    track_id = int(value[2:], 16)
    if track_id <= 0 or track_id > 0xFFFF_FFFF:
        return "unresolved", None
    return "valid", track_id


def parse_probe_signal(raw: bytes) -> ProbeSignal:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_probe_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise BoundedProcessError("log_probe_output_invalid") from exc
    if (
        not isinstance(value, dict)
        or "streams" not in value
        or not set(value).issubset(PROBE_TOP_LEVEL_FIELDS)
        or any(value.get(field) != [] for field in ("programs", "stream_groups") if field in value)
    ):
        raise BoundedProcessError("log_probe_output_invalid")
    streams = value["streams"]
    if not isinstance(streams, list) or len(streams) != 1:
        raise BoundedProcessError("log_probe_output_invalid")
    stream = streams[0]
    if not isinstance(stream, dict) or not set(stream).issubset(PROBE_STREAM_FIELDS):
        raise BoundedProcessError("log_probe_output_invalid")
    side_data = stream.get("side_data_list", [])
    if not isinstance(side_data, list) or any(item != {} for item in side_data):
        raise BoundedProcessError("log_probe_output_invalid")
    index = stream.get("index")
    if type(index) is not int or index < 0:
        raise BoundedProcessError("log_probe_output_invalid")
    codec_type = _optional_probe_text(stream, "codec_type")
    if codec_type not in {None, "video"}:
        raise BoundedProcessError("log_probe_output_invalid")
    track_id_status, track_id = parse_stream_id(stream.get("id"))
    return ProbeSignal(
        index=index,
        track_id_status=track_id_status,
        track_id=track_id,
        codec_type=codec_type,
        color_space=_optional_probe_text(stream, "color_space"),
        color_transfer=_optional_probe_text(stream, "color_transfer"),
        color_primaries=_optional_probe_text(stream, "color_primaries"),
    )


def _optional_probe_text(value: dict[str, Any], field: str) -> str | None:
    member = value.get(field)
    if member is None:
        return None
    if not isinstance(member, str) or not member or len(member) > 64 or "\x00" in member:
        raise BoundedProcessError("log_probe_output_invalid")
    return member


def track_ids_correlate(container: ContainerSignal, probe: ProbeSignal) -> bool:
    return (
        container.track_resolution == "matched"
        and container.track_id is not None
        and probe.track_id_status == "valid"
        and probe.track_id is not None
        and container.track_id == probe.track_id
    )


def profile_colors_are_allowed(
    *,
    source_profile: str,
    probe: ProbeSignal,
    manifest: DetectorManifest,
) -> bool:
    allowlist = next(
        (
            item
            for item in manifest.color_allowlists
            if item.source_profile == source_profile
        ),
        None,
    )
    if allowlist is None:
        return False
    return (
        probe.color_primaries in allowlist.color_primaries
        and probe.color_transfer in allowlist.color_transfer
        and probe.color_space in allowlist.color_space
    )


def is_exact_not_log_probe(probe: ProbeSignal, manifest: DetectorManifest) -> bool:
    predicate = manifest.not_log_predicate
    return (
        probe.color_primaries == predicate.color_primaries
        and probe.color_transfer == predicate.color_transfer
        and probe.color_space == predicate.color_space
    )


def classify_detection(
    *,
    container: ContainerSignal,
    probe: ProbeSignal,
    manifest: DetectorManifest,
) -> DetectionResult:
    status = "unknown"
    source_profile = None
    if container.kind == "recognized_logs":
        if (
            container.source_profile is not None
            and track_ids_correlate(container, probe)
            and profile_colors_are_allowed(
                source_profile=container.source_profile,
                probe=probe,
                manifest=manifest,
            )
        ):
            status = "apple_log"
            source_profile = container.source_profile
    elif container.kind == "no_logs":
        if track_ids_correlate(container, probe) and is_exact_not_log_probe(
            probe, manifest
        ):
            status = "not_log"
    elif container.kind == "unsupported_container":
        if is_exact_not_log_probe(probe, manifest):
            status = "not_log"
    elif container.kind in {"unknown_logs", "conflicting_logs"}:
        pass
    elif container.kind == "invalid":
        raise BoundedProcessError("log_container_invalid")
    elif container.kind == "resource_limit":
        raise BoundedProcessError("log_container_resource_limit")
    else:
        raise BoundedProcessError("log_probe_output_invalid")

    evidence_json = canonical_document(
        {
            "classification": status,
            "color": {
                "color_primaries": _normalize_color(probe.color_primaries),
                "color_space": _normalize_color(probe.color_space),
                "color_transfer": _normalize_color(probe.color_transfer),
            },
            "parser_contract_version": manifest.parser_contract_version,
            "signal_kind": container.signal_kind,
            "source_profile": source_profile,
        }
    )
    if len(evidence_json) > manifest.max_evidence_bytes:
        raise BoundedProcessError("log_probe_output_invalid")
    return DetectionResult(
        status=status,
        source_profile=source_profile,
        evidence_sha256=hashlib.sha256(evidence_json).hexdigest(),
        evidence_json=evidence_json,
    )


def _normalize_color(value: str | None) -> str | None:
    return None if value in {None, "unknown"} else value


def serialize_detection_identity(
    result: DetectionResult, *, manifest: DetectorManifest
) -> dict[str, str | None]:
    return {
        "detection_status": result.status,
        "source_profile": result.source_profile,
        "detector_rule_version": manifest.rule_version,
        "detector_manifest_sha256": manifest.manifest_sha256,
        "detector_evidence_sha256": result.evidence_sha256,
    }


def probe_and_classify(
    *,
    ffprobe_binary: str,
    source_path: Path,
    manifest: DetectorManifest,
) -> DetectionResult:
    argv = [ffprobe_binary, *FFPROBE_PROBE_ARGUMENTS, str(source_path)]
    result = run_bounded_process(
        argv,
        timeout_ms=manifest.timeout_ms,
        max_stdout_bytes=manifest.max_stdout_bytes,
        max_stderr_bytes=manifest.max_stderr_bytes,
    )
    return classify_probe_bytes(result.stdout, manifest=manifest)


def classify_probe_bytes(raw: bytes, *, manifest: DetectorManifest) -> DetectionResult:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_probe_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise BoundedProcessError("log_probe_output_invalid") from exc
    if not isinstance(value, dict):
        raise BoundedProcessError("log_probe_output_invalid")

    selected: tuple[Predicate, ...] = ()
    status = "unknown"
    if _matches_all(value, manifest.apple_log):
        selected = manifest.apple_log
        status = "apple_log"
    elif _matches_all(value, manifest.not_log):
        selected = manifest.not_log
        status = "not_log"

    evidence = []
    for predicate in sorted(selected, key=lambda item: item.path):
        present, actual = _resolve_path(value, predicate.path)
        if present:
            evidence.append({"path": predicate.path, "value": actual})
    evidence_json = canonical_document({"classification": status, "values": evidence})
    if len(evidence_json) > manifest.max_evidence_bytes:
        raise BoundedProcessError("log_probe_output_invalid")
    return DetectionResult(
        status=status,
        source_profile=None,
        evidence_sha256=hashlib.sha256(evidence_json).hexdigest(),
        evidence_json=evidence_json,
    )


def read_ffprobe_version(
    *,
    ffprobe_binary: str,
    timeout_ms: int,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
) -> str:
    result = run_bounded_process(
        [ffprobe_binary, "-version"],
        timeout_ms=timeout_ms,
        max_stdout_bytes=max_stdout_bytes,
        max_stderr_bytes=max_stderr_bytes,
    )
    try:
        first_line = result.stdout.decode("utf-8", errors="strict").splitlines()[0].strip()
    except (UnicodeError, IndexError) as exc:
        raise DetectorValidationError("log_detector_version_mismatch") from exc
    if not first_line or len(first_line) > 256:
        raise DetectorValidationError("log_detector_version_mismatch")
    return first_line


def _matches_all(value: dict[str, Any], predicates: tuple[Predicate, ...]) -> bool:
    for predicate in predicates:
        present, actual = _resolve_path(value, predicate.path)
        if predicate.operator == "present":
            if not present:
                return False
        elif not present or actual != predicate.expected_value:
            return False
    return True


def _resolve_path(value: dict[str, Any], path: str) -> tuple[bool, Any]:
    for prefix, parent_path in (
        ("format.tags.", ("format", "tags")),
        ("streams.0.tags.", ("streams", "0", "tags")),
        ("streams.0.disposition.", ("streams", "0", "disposition")),
    ):
        if path.startswith(prefix):
            present, parent = _resolve_components(
                value, parent_path, allow_container=True
            )
            key = path.removeprefix(prefix)
            if present and isinstance(parent, dict) and key in parent:
                result = parent[key]
                return (False, None) if isinstance(result, (dict, list)) else (True, result)
            return False, None
    return _resolve_components(value, tuple(path.split(".")))


def _resolve_components(
    value: dict[str, Any],
    components: tuple[str, ...],
    *,
    allow_container: bool = False,
) -> tuple[bool, Any]:
    current: Any = value
    for component in components:
        if isinstance(current, list):
            if component != "0" or not current:
                return False, None
            current = current[0]
        elif isinstance(current, dict) and component in current:
            current = current[component]
        else:
            return False, None
    if not allow_container and isinstance(current, (dict, list)):
        return False, None
    return True, current


def _unique_probe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate probe field")
        result[key] = value
    return result
