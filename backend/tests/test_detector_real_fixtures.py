from __future__ import annotations

from pathlib import Path

import pytest

from app.services.apple_log_detector import classify_detection
from app.services.detector_certification import (
    _ephemeral_manifest,
    resolve_certification_fixtures,
    verify_fixture_media,
)
from app.services.detector_inspection import inspect_fixture_path
from app.services.detector_manifest import load_rule_input


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPOSITORY_ROOT / "data"
RULE_PATH = (
    REPOSITORY_ROOT
    / "backend/assets/detectors/apple-log-v2/detector-rule-input-v2.json"
)


def _real_fixtures():
    descriptor = FIXTURE_ROOT / "detector-certification-v2.json"
    if not descriptor.exists():
        pytest.skip("local detector certification fixtures are unavailable")
    return {
        item.input.role: verify_fixture_media(item)
        for item in resolve_certification_fixtures(FIXTURE_ROOT)
    }


@pytest.mark.parametrize(
    ("role", "expected_status", "expected_profile", "expected_evidence_class"),
    [
        ("apple-log-2", "apple_log", "apple-log-2", "real-container"),
        ("ordinary", "not_log", None, "real-container"),
    ],
)
def test_local_real_container_fixture_classification(
    role,
    expected_status,
    expected_profile,
    expected_evidence_class,
):
    fixture = _real_fixtures()[role]
    rule = load_rule_input(RULE_PATH)
    manifest = _ephemeral_manifest(rule, "local-ffprobe")

    inspection = inspect_fixture_path(fixture.media_path)
    result = classify_detection(
        container=inspection.container,
        probe=inspection.probe,
        manifest=manifest,
    )

    assert fixture.input.evidence_class == expected_evidence_class
    assert result.status == expected_status
    assert result.source_profile == expected_profile
