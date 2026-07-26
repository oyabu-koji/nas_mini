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
        "schema_version": 1,
        "detector_id": "apple-log-v1",
        "rule_version": "test-v1",
        "apple_log": [apple],
        "not_log": [ordinary],
        "approval": {
            "approving_role": "repository-owner",
            "approved_at": "2026-07-24T12:00:00+09:00",
            "approval_reference": "test-review-only",
        },
    }
    rule_bytes = canonical_document(rule)
    rule_path = root / "detector-rule-input-v1.json"
    rule_path.write_bytes(rule_bytes)
    rule_digest = hashlib.sha256(rule_bytes).hexdigest()
    rule_path.with_suffix(".sha256").write_text(rule_digest + "\n", encoding="ascii")

    fixtures = [
        {
            "role": "apple_log",
            "sha256": "a" * 64,
            "expected_classification": "apple_log",
            "source_label": "user-owned-local-recording",
        },
        {
            "role": "ordinary",
            "sha256": "b" * 64,
            "expected_classification": "not_log",
            "source_label": "user-owned-local-recording",
        },
    ]
    manifest = {
        "schema_version": 1,
        "detector_id": "apple-log-v1",
        "rule_version": "test-v1",
        "rule_input_sha256": rule_digest,
        "rules": {"apple_log": [apple], "not_log": [ordinary]},
        "ffprobe_version": "ffprobe test pinned",
        "show_entries": FFPROBE_SHOW_ENTRIES,
        "timeout_ms": 15_000,
        "max_stdout_bytes": 1_048_576,
        "max_stderr_bytes": 1_048_576,
        "max_evidence_bytes": 4_096,
        "fixtures": fixtures,
        "source_reference": "https://example.invalid/technical-reference",
    }
    manifest_bytes = document_with_digest(manifest, "manifest_sha256")
    (root / "manifest.json").write_bytes(manifest_bytes)
    manifest_digest = __import__("json").loads(manifest_bytes)["manifest_sha256"]
    summary = {
        "schema_version": 1,
        "manifest_sha256": manifest_digest,
        "rule_input_sha256": rule_digest,
        "ffprobe_version": "ffprobe test pinned",
        "fixtures": [
            {"role": "apple_log", "sha256": "a" * 64},
            {"role": "ordinary", "sha256": "b" * 64},
        ],
    }
    (root / "certificate-summary.json").write_bytes(canonical_document(summary))
    return rule_path, rule, manifest
