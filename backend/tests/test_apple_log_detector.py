import json
import hashlib
from dataclasses import replace

import pytest

from app.services.apple_log_detector import (
    classify_probe_bytes,
    probe_and_classify,
    serialize_detection_identity,
)
from app.services.bounded_subprocess import BoundedProcessResult
from app.services.bounded_subprocess import BoundedProcessError
from app.services.detector_manifest import (
    DETECTOR_MAX_STDERR_BYTES,
    DETECTOR_MAX_STDOUT_BYTES,
    DETECTOR_PROBE_TIMEOUT_MS,
    FFPROBE_PROBE_ARGUMENTS,
    Predicate,
    load_detector_manifest,
    load_rule_input,
)
from tests.detector_test_support import write_detector_artifacts


def manifest(tmp_path):
    rule_path, _rule, _manifest = write_detector_artifacts(tmp_path)
    rule = load_rule_input(rule_path)
    return load_detector_manifest(tmp_path / "manifest.json", rule_input=rule)


def test_detector_evaluates_apple_log_then_not_log_then_unknown(tmp_path):
    detector = manifest(tmp_path)
    apple = {
        "streams": [
            {
                "tags": {"transfer_characteristic": "Apple Log"},
                "color_transfer": "bt709",
            }
        ]
    }
    ordinary = {"streams": [{"color_transfer": "bt709", "tags": {}}]}
    unknown = {"streams": [{"color_transfer": "smpte2084", "tags": {}}]}

    apple_result = classify_probe_bytes(json.dumps(apple).encode(), manifest=detector)
    ordinary_result = classify_probe_bytes(json.dumps(ordinary).encode(), manifest=detector)
    unknown_result = classify_probe_bytes(json.dumps(unknown).encode(), manifest=detector)

    assert apple_result.status == "apple_log"
    assert ordinary_result.status == "not_log"
    assert unknown_result.status == "unknown"
    assert b"Apple Log" in apple_result.evidence_json
    assert b"smpte2084" not in unknown_result.evidence_json
    assert len(apple_result.evidence_json) <= 4096


def test_filename_and_legacy_hint_cannot_change_classification(tmp_path):
    detector = manifest(tmp_path)
    raw = json.dumps(
        {
            "format": {"filename": "apple-log.mov", "tags": {"is_log": "true"}},
            "streams": [{"tags": {}, "color_transfer": "smpte2084"}],
        }
    ).encode()

    result = classify_probe_bytes(raw, manifest=detector)

    assert result.status == "unknown"
    assert b"filename" not in result.evidence_json
    assert b"is_log" not in result.evidence_json


def test_runtime_probe_uses_exact_certified_argv_and_limits(tmp_path, monkeypatch):
    detector = manifest(tmp_path)
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return BoundedProcessResult(stdout=b'{"streams":[]}', stderr=b"", returncode=0)

    monkeypatch.setattr("app.services.apple_log_detector.run_bounded_process", fake_run)

    probe_and_classify(
        ffprobe_binary="/usr/bin/ffprobe",
        source_path=tmp_path / "source.mov",
        manifest=detector,
    )

    assert captured["argv"] == [
        "/usr/bin/ffprobe",
        *FFPROBE_PROBE_ARGUMENTS,
        str(tmp_path / "source.mov"),
    ]
    assert captured["kwargs"] == {
        "timeout_ms": DETECTOR_PROBE_TIMEOUT_MS,
        "max_stdout_bytes": DETECTOR_MAX_STDOUT_BYTES,
        "max_stderr_bytes": DETECTOR_MAX_STDERR_BYTES,
    }


def test_detector_resolves_dotted_quicktime_tag_as_one_allowlisted_key(tmp_path):
    detector = manifest(tmp_path)
    dotted = replace(
        detector,
        apple_log=(
            Predicate(
                path="format.tags.com.apple.quicktime.camera.identifier",
                operator="equals",
                expected_value="iPhone",
                rationale="test",
                source_reference="test",
            ),
        ),
    )

    result = classify_probe_bytes(
        b'{"format":{"tags":{"com.apple.quicktime.camera.identifier":"iPhone"}}}',
        manifest=dotted,
    )

    assert result.status == "apple_log"


def test_absent_conflicting_and_unsupported_metadata_are_unknown(tmp_path):
    detector = manifest(tmp_path)
    detector = replace(
        detector,
        apple_log=detector.apple_log
        + (
            Predicate(
                path="streams.0.color_transfer",
                operator="equals",
                expected_value="arib-std-b67",
                rationale="test all-of",
                source_reference="test",
            ),
        ),
    )
    probes = [
        b'{"streams":[{"tags":{}}]}',
        b'{"streams":[{"tags":{"transfer_characteristic":"Apple Log"},'
        b'"color_transfer":"smpte2084"}]}',
        b'{"streams":[{"tags":{"transfer_characteristic":"Apple Log 2"},'
        b'"color_transfer":"smpte2084"}]}',
    ]

    assert [
        classify_probe_bytes(probe, manifest=detector).status for probe in probes
    ] == ["unknown", "unknown", "unknown"]


@pytest.mark.parametrize(
    "raw",
    [
        b'{"streams":[],"streams":[]}',
        b"[]",
        b'{"streams":NaN}',
        b"\xff",
    ],
)
def test_malformed_probe_output_is_terminal_not_unknown(tmp_path, raw):
    with pytest.raises(BoundedProcessError) as raised:
        classify_probe_bytes(raw, manifest=manifest(tmp_path))

    assert raised.value.code == "log_probe_output_invalid"


def test_evidence_is_sorted_bounded_jcs_and_digest_only_selected_values(tmp_path):
    detector = manifest(tmp_path)
    detector = replace(
        detector,
        apple_log=(
            Predicate(
                path="streams.0.tags.z_tag",
                operator="equals",
                expected_value="z",
                rationale="test",
                source_reference="test",
            ),
            Predicate(
                path="streams.0.tags.a_tag",
                operator="equals",
                expected_value="a",
                rationale="test",
                source_reference="test",
            ),
        ),
    )
    raw = (
        b'{"streams":[{"tags":{"z_tag":"z","a_tag":"a","secret":"do-not-store"}}],'
        b'"format":{"filename":"private.mov"}}'
    )

    result = classify_probe_bytes(raw, manifest=detector)

    assert json.loads(result.evidence_json)["values"] == [
        {"path": "streams.0.tags.a_tag", "value": "a"},
        {"path": "streams.0.tags.z_tag", "value": "z"},
    ]
    assert result.evidence_sha256 == hashlib.sha256(result.evidence_json).hexdigest()
    assert b"secret" not in result.evidence_json
    assert b"private.mov" not in result.evidence_json

    oversized = replace(
        detector,
        apple_log=(
            replace(detector.apple_log[0], expected_value="x" * 4096),
        ),
    )
    with pytest.raises(BoundedProcessError) as raised:
        classify_probe_bytes(
            json.dumps(
                {"streams": [{"tags": {"z_tag": "x" * 4096}}]}
            ).encode(),
            manifest=oversized,
        )
    assert raised.value.code == "log_probe_output_invalid"


def test_detection_serializer_never_exposes_raw_evidence(tmp_path):
    detector = manifest(tmp_path)
    result = classify_probe_bytes(
        b'{"streams":[{"tags":{"transfer_characteristic":"Apple Log"}}]}',
        manifest=detector,
    )

    serialized = serialize_detection_identity(result, manifest=detector)

    assert serialized["detection_status"] == "apple_log"
    assert serialized["detector_evidence_sha256"] == result.evidence_sha256
    assert "evidence_json" not in serialized
    assert b"Apple Log" not in json.dumps(serialized).encode()
