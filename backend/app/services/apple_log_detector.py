from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.bounded_subprocess import BoundedProcessError, run_bounded_process
from app.services.detector_manifest import (
    DetectorManifest,
    DetectorValidationError,
    FFPROBE_PROBE_ARGUMENTS,
    Predicate,
    canonical_document,
)


@dataclass(frozen=True)
class DetectionResult:
    status: str
    source_profile: str | None
    evidence_sha256: str
    evidence_json: bytes


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
