from __future__ import annotations

import hashlib
from pathlib import Path

from app.services.detector_manifest import (
    FFPROBE_SHOW_ENTRIES,
    canonical_document,
    document_with_digest,
)


def predicate(path: str, expected_value: str) -> dict[str, object]:
    return {
        "path": path,
        "operator": "equals",
        "expected_value": expected_value,
        "rationale": "sanitized test rule",
        "source_reference": "https://example.invalid/technical-reference",
    }


def write_detector_artifacts(root: Path) -> tuple[Path, dict[str, object], dict[str, object]]:
    root.mkdir(parents=True, exist_ok=True)
    apple = predicate("streams.0.tags.transfer_characteristic", "Apple Log")
    ordinary = predicate("streams.0.color_transfer", "bt709")
    rule = {
        "schema_version": 2,
        "detector_id": "apple-log-v2",
        "rule_version": "test-v1",
        "parser_contract_version": "iso-bmff-apple-log-v1",
        "identifier_mappings": [
            {
                "identifier": "com.apple.rec2020.apple-log",
                "source_profile": "apple-log-1",
                "signal_kind": "apple-log-1-logs",
                "rationale": "Apple documents this as the Apple Log 1 identifier.",
                "source_reference": "https://developer.apple.com/documentation/videotoolbox/kvtcompressionpropertykey_logtransferfunction",
            },
            {
                "identifier": "com.apple.apple-wide-gamut.apple-log",
                "source_profile": "apple-log-2",
                "signal_kind": "apple-log-2-logs",
                "rationale": "Apple documents this as the Apple Log 2 identifier.",
                "source_reference": "https://developer.apple.com/documentation/videotoolbox/kvtcompressionpropertykey_logtransferfunction",
            },
        ],
        "profile_preset_mappings": [
            {
                "source_profile": "apple-log-1",
                "requested_preset_id": "generated-apple-log-rec709",
            },
            {
                "source_profile": "apple-log-2",
                "requested_preset_id": "generated-apple-log2-rec709",
            },
        ],
        "color_allowlists": [
            {
                "source_profile": "apple-log-1",
                "color_primaries": [None, "unknown", "bt2020"],
                "color_transfer": [None, "unknown"],
                "color_space": [None, "unknown", "bt2020nc"],
            },
            {
                "source_profile": "apple-log-2",
                "color_primaries": [None, "unknown"],
                "color_transfer": [None, "unknown"],
                "color_space": [None, "unknown", "bt2020nc"],
            },
        ],
        "not_log_predicate": {
            "color_primaries": "bt709",
            "color_transfer": "bt709",
            "color_space": "bt709",
        },
        "resource_limits": {
            "file_size_bytes": 1_099_511_627_776,
            "box_headers": 65_536,
            "nesting_depth": 12,
            "video_tracks": 8,
            "sample_descriptions": 32,
            "metadata_bytes": 1_048_576,
            "retained_identifiers": 16,
        },
        "official_source_url": "https://developer.apple.com/documentation/videotoolbox/kvtcompressionpropertykey_logtransferfunction",
        "approval": {
            "approving_role": "repository-owner",
            "approved_at": "2026-07-24T12:00:00+09:00",
            "approval_reference": "test-review-only",
        },
    }
    rule_bytes = canonical_document(rule)
    rule_path = root / "detector-rule-input-v2.json"
    rule_path.write_bytes(rule_bytes)
    rule_digest = hashlib.sha256(rule_bytes).hexdigest()
    (rule_path.parent / f"{rule_path.name}.sha256").write_text(
        rule_digest + "\n",
        encoding="ascii",
    )

    fixtures = [
        {
            "role": "apple-log-1",
            "evidence_class": "synthetic-container",
            "sha256": "c" * 64,
            "expected_detection_status": "apple_log",
            "expected_source_profile": "apple-log-1",
            "provenance": "project-owned-synthetic-container",
        },
        {
            "role": "apple-log-2",
            "evidence_class": "real-container",
            "sha256": "a" * 64,
            "expected_detection_status": "apple_log",
            "expected_source_profile": "apple-log-2",
            "provenance": "user-owned-local-recording",
        },
        {
            "role": "ordinary",
            "evidence_class": "real-container",
            "sha256": "b" * 64,
            "expected_detection_status": "not_log",
            "expected_source_profile": None,
            "provenance": "user-owned-local-recording",
        },
    ]
    manifest = {
        "schema_version": 2,
        "detector_id": "apple-log-v2",
        "rule_version": "test-v1",
        "rule_input_sha256": rule_digest,
        "parser_contract_version": rule["parser_contract_version"],
        "identifier_mappings": rule["identifier_mappings"],
        "profile_preset_mappings": rule["profile_preset_mappings"],
        "color_allowlists": rule["color_allowlists"],
        "not_log_predicate": rule["not_log_predicate"],
        "resource_limits": rule["resource_limits"],
        "official_source_url": rule["official_source_url"],
        "ffprobe_version": "ffprobe test pinned",
        "show_entries": FFPROBE_SHOW_ENTRIES,
        "timeout_ms": 15_000,
        "max_stdout_bytes": 1_048_576,
        "max_stderr_bytes": 1_048_576,
        "max_evidence_bytes": 4_096,
        "fixtures": fixtures,
    }
    manifest_bytes = document_with_digest(manifest, "manifest_sha256")
    (root / "manifest.json").write_bytes(manifest_bytes)
    manifest_digest = __import__("json").loads(manifest_bytes)["manifest_sha256"]
    summary = {
        "schema_version": 2,
        "detector_id": "apple-log-v2",
        "manifest_sha256": manifest_digest,
        "rule_input_sha256": rule_digest,
        "parser_contract_version": "iso-bmff-apple-log-v1",
        "ffprobe_version": "ffprobe test pinned",
        "future_apple_log_1_transform_allowed": False,
        "fixtures": [
            {
                "role": item["role"],
                "evidence_class": item["evidence_class"],
                "sha256": item["sha256"],
                "expected_detection_status": item["expected_detection_status"],
                "expected_source_profile": item["expected_source_profile"],
            }
            for item in fixtures
        ],
    }
    (root / "certificate-summary.json").write_bytes(canonical_document(summary))
    return rule_path, rule, manifest
