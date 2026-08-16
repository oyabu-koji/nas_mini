import json
import hashlib

import pytest

from app.services.apple_log_detector import (
    classify_detection,
    parse_probe_signal,
    profile_colors_are_allowed,
    is_exact_not_log_probe,
    parse_stream_id,
    serialize_detection_identity,
    track_ids_correlate,
)
from app.services.bounded_subprocess import BoundedProcessError
from app.services.detector_manifest import (
    canonical_document,
    load_detector_manifest,
    load_rule_input,
)
from app.services.iso_bmff_log_parser import ContainerSignal
from tests.detector_test_support import write_detector_artifacts


def manifest(tmp_path):
    rule_path, _rule, _manifest = write_detector_artifacts(tmp_path)
    rule = load_rule_input(rule_path)
    return load_detector_manifest(tmp_path / "manifest.json", rule_input=rule)


def container_signal(
    kind,
    *,
    source_profile=None,
    track_id=None,
    track_resolution="not_applicable",
    signal_kind=None,
):
    return ContainerSignal(
        kind=kind,
        source_profile=source_profile,
        track_id=track_id,
        track_resolution=track_resolution,
        signal_kind=signal_kind,
        box_headers=8,
        max_depth_seen=8,
        metadata_bytes_read=128,
    )


@pytest.mark.parametrize(
    "value",
    [None, 1, True, "1", "0X1", "0xA", "0x1 ", " 0x1", "0x-1"],
)
def test_ffprobe_stream_id_parser_requires_strict_lowercase_hex(value):
    assert parse_stream_id(value) == ("unresolved", None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0x1", 1), ("0x80000001", 0x8000_0001), ("0xffffffff", 0xFFFF_FFFF)],
)
def test_valid_stream_id_normalizes_to_nonzero_uint32(value, expected):
    assert parse_stream_id(value) == ("valid", expected)


@pytest.mark.parametrize(
    "value",
    [None, 7, "7", "0X7", "0x0", "0x100000000", "0xgg"],
)
def test_unusable_stream_id_normalizes_to_unresolved(value):
    assert parse_stream_id(value) == ("unresolved", None)


def test_unresolved_stream_id_keeps_bounded_color_fields():
    probe = parse_probe_signal(
        b'{"streams":[{"index":0,"id":"not-hex","codec_type":"video",'
        b'"color_space":"bt2020nc","color_transfer":"unknown",'
        b'"color_primaries":"bt2020"}]}'
    )

    assert probe.track_id_status == "unresolved"
    assert probe.track_id is None
    assert probe.color_space == "bt2020nc"
    assert probe.color_transfer == "unknown"
    assert probe.color_primaries == "bt2020"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"streams":[]}',
        b'{"streams":[{"index":0},{"index":1}]}',
    ],
)
def test_probe_requires_exactly_one_selected_stream(raw):
    with pytest.raises(BoundedProcessError) as raised:
        parse_probe_signal(raw)

    assert raised.value.code == "log_probe_output_invalid"


def test_probe_strictly_accepts_only_empty_ffprobe_wrapper_sections():
    probe = parse_probe_signal(
        b'{"programs":[],"stream_groups":[],"streams":[{"index":0,'
        b'"id":"0x7","side_data_list":[{}]}]}'
    )

    assert probe.track_id == 7


@pytest.mark.parametrize(
    "raw",
    [
        b'{"programs":[{}],"streams":[{"index":0}]}',
        b'{"stream_groups":[{}],"streams":[{"index":0}]}',
        b'{"streams":[{"index":0,"side_data_list":[{"private":"value"}]}]}',
        b'{"unknown":[],"streams":[{"index":0}]}',
    ],
)
def test_probe_rejects_nonempty_or_unknown_ffprobe_wrapper_sections(raw):
    with pytest.raises(BoundedProcessError) as raised:
        parse_probe_signal(raw)

    assert raised.value.code == "log_probe_output_invalid"


@pytest.mark.parametrize(
    ("container_track_id", "probe_id", "expected"),
    [(7, "0x7", True), (7, "0x8", False), (7, None, False)],
)
def test_parser_track_id_is_correlated_with_ffprobe_id(
    container_track_id,
    probe_id,
    expected,
):
    container = ContainerSignal(
        kind="recognized_logs",
        source_profile="apple-log-1",
        track_id=container_track_id,
        track_resolution="matched",
        signal_kind="apple-log-1-logs",
        box_headers=8,
        max_depth_seen=8,
        metadata_bytes_read=128,
    )
    probe = parse_probe_signal(
        json.dumps({"streams": [{"index": 0, "id": probe_id}]}).encode()
    )

    assert track_ids_correlate(container, probe) is expected


@pytest.mark.parametrize(
    ("primaries", "transfer", "space", "expected"),
    [
        (None, None, None, True),
        ("unknown", "unknown", "unknown", True),
        ("bt2020", None, "bt2020nc", True),
        ("bt709", None, None, False),
        (None, "bt709", None, False),
        (None, None, "bt709", False),
    ],
)
def test_apple_log_1_color_allowlist_classifier(
    tmp_path,
    primaries,
    transfer,
    space,
    expected,
):
    detector = manifest(tmp_path)
    probe = parse_probe_signal(
        json.dumps(
            {
                "streams": [
                    {
                        "index": 0,
                        "id": "0x7",
                        "color_primaries": primaries,
                        "color_transfer": transfer,
                        "color_space": space,
                    }
                ]
            }
        ).encode()
    )

    assert profile_colors_are_allowed(
        source_profile="apple-log-1",
        probe=probe,
        manifest=detector,
    ) is expected


@pytest.mark.parametrize(
    ("primaries", "transfer", "space", "expected"),
    [
        (None, None, None, True),
        ("unknown", "unknown", "unknown", True),
        (None, None, "bt2020nc", True),
        ("bt2020", None, "bt2020nc", False),
        (None, "bt709", None, False),
        (None, None, "bt709", False),
    ],
)
def test_apple_log_2_color_allowlist_classifier(
    tmp_path,
    primaries,
    transfer,
    space,
    expected,
):
    detector = manifest(tmp_path)
    probe = parse_probe_signal(
        json.dumps(
            {
                "streams": [
                    {
                        "index": 0,
                        "id": "0x7",
                        "color_primaries": primaries,
                        "color_transfer": transfer,
                        "color_space": space,
                    }
                ]
            }
        ).encode()
    )

    assert profile_colors_are_allowed(
        source_profile="apple-log-2",
        probe=probe,
        manifest=detector,
    ) is expected


@pytest.mark.parametrize(
    ("primaries", "transfer", "space", "expected"),
    [
        ("bt709", "bt709", "bt709", True),
        (None, "bt709", "bt709", False),
        ("bt709", None, "bt709", False),
        ("bt709", "bt709", None, False),
        ("unknown", "bt709", "bt709", False),
    ],
)
def test_triple_bt709_classifier_is_exact(
    tmp_path,
    primaries,
    transfer,
    space,
    expected,
):
    detector = manifest(tmp_path)
    probe = parse_probe_signal(
        json.dumps(
            {
                "streams": [
                    {
                        "index": 0,
                        "color_primaries": primaries,
                        "color_transfer": transfer,
                        "color_space": space,
                    }
                ]
            }
        ).encode()
    )

    assert is_exact_not_log_probe(probe, detector) is expected


@pytest.mark.parametrize(
    ("container", "probe_fields", "expected_status", "expected_profile"),
    [
        (
            container_signal(
                "recognized_logs",
                source_profile="apple-log-1",
                track_id=7,
                track_resolution="matched",
                signal_kind="apple-log-1-logs",
            ),
            {"id": "0x7"},
            "apple_log",
            "apple-log-1",
        ),
        (
            container_signal(
                "recognized_logs",
                source_profile="apple-log-1",
                track_id=7,
                track_resolution="matched",
                signal_kind="apple-log-1-logs",
            ),
            {"id": "0x8"},
            "unknown",
            None,
        ),
        (
            container_signal(
                "no_logs",
                track_id=7,
                track_resolution="matched",
                signal_kind="no-logs",
            ),
            {
                "id": "0x7",
                "color_primaries": "bt709",
                "color_transfer": "bt709",
                "color_space": "bt709",
            },
            "not_log",
            None,
        ),
        (
            container_signal(
                "unsupported_container",
                signal_kind="unsupported-container",
            ),
            {
                "color_primaries": "bt709",
                "color_transfer": "bt709",
                "color_space": "bt709",
            },
            "not_log",
            None,
        ),
        (
            container_signal(
                "conflicting_logs",
                track_id=7,
                track_resolution="matched",
                signal_kind="conflicting-logs",
            ),
            {
                "id": "0x7",
                "color_primaries": "bt709",
                "color_transfer": "bt709",
                "color_space": "bt709",
            },
            "unknown",
            None,
        ),
    ],
)
def test_parser_result_classification_table_is_closed(
    tmp_path,
    container,
    probe_fields,
    expected_status,
    expected_profile,
):
    probe = parse_probe_signal(
        json.dumps({"streams": [{"index": 0, **probe_fields}]}).encode()
    )

    result = classify_detection(
        container=container,
        probe=probe,
        manifest=manifest(tmp_path),
    )

    assert result.status == expected_status
    assert result.source_profile == expected_profile


def test_invalid_container_maps_to_stable_detector_error(tmp_path):
    probe = parse_probe_signal(b'{"streams":[{"index":0}]}')

    with pytest.raises(BoundedProcessError) as raised:
        classify_detection(
            container=container_signal("invalid"),
            probe=probe,
            manifest=manifest(tmp_path),
        )

    assert raised.value.code == "log_container_invalid"


def test_resource_limited_container_maps_to_stable_detector_error(tmp_path):
    probe = parse_probe_signal(b'{"streams":[{"index":0}]}')

    with pytest.raises(BoundedProcessError) as raised:
        classify_detection(
            container=container_signal("resource_limit"),
            probe=probe,
            manifest=manifest(tmp_path),
        )

    assert raised.value.code == "log_container_resource_limit"


def test_canonical_evidence_v2_has_closed_shape(tmp_path):
    detector = manifest(tmp_path)
    probe = parse_probe_signal(
        b'{"streams":[{"index":0,"id":"0x7","codec_type":"video",'
        b'"color_space":"bt2020nc","color_transfer":"unknown",'
        b'"color_primaries":"unknown"}]}'
    )
    result = classify_detection(
        container=container_signal(
            "recognized_logs",
            source_profile="apple-log-2",
            track_id=7,
            track_resolution="matched",
            signal_kind="apple-log-2-logs",
        ),
        probe=probe,
        manifest=detector,
    )

    assert json.loads(result.evidence_json) == {
        "classification": "apple_log",
        "color": {
            "color_primaries": None,
            "color_space": "bt2020nc",
            "color_transfer": None,
        },
        "parser_contract_version": "iso-bmff-apple-log-v1",
        "signal_kind": "apple-log-2-logs",
        "source_profile": "apple-log-2",
    }
    assert result.evidence_json == canonical_document(
        json.loads(result.evidence_json)
    )


def test_evidence_and_api_identity_exclude_track_and_raw_metadata(tmp_path):
    detector = manifest(tmp_path)
    result = classify_detection(
        container=container_signal(
            "recognized_logs",
            source_profile="apple-log-1",
            track_id=0xDEADBEEF,
            track_resolution="matched",
            signal_kind="apple-log-1-logs",
        ),
        probe=parse_probe_signal(
            b'{"streams":[{"index":0,"id":"0xdeadbeef"}]}'
        ),
        manifest=detector,
    )

    evidence = json.loads(result.evidence_json)
    serialized = serialize_detection_identity(result, manifest=detector)

    assert set(evidence) == {
        "classification",
        "color",
        "parser_contract_version",
        "signal_kind",
        "source_profile",
    }
    assert "track_id" not in evidence
    assert "track_resolution" not in evidence
    assert "identifier" not in evidence
    assert "path" not in evidence
    assert "raw_metadata" not in evidence
    assert "track_id" not in serialized
    assert "track_resolution" not in serialized
    assert b"deadbeef" not in result.evidence_json


def test_canonical_evidence_digest_is_stable_for_equivalent_probe_values(tmp_path):
    detector = manifest(tmp_path)
    container = container_signal(
        "recognized_logs",
        source_profile="apple-log-2",
        track_id=7,
        track_resolution="matched",
        signal_kind="apple-log-2-logs",
    )
    missing = parse_probe_signal(
        b'{"streams":[{"index":0,"id":"0x7","color_space":"bt2020nc"}]}'
    )
    explicit_unknown = parse_probe_signal(
        b'{"streams":[{"color_transfer":"unknown","id":"0x7",'
        b'"color_primaries":"unknown","index":0,"color_space":"bt2020nc"}]}'
    )

    first = classify_detection(
        container=container,
        probe=missing,
        manifest=detector,
    )
    second = classify_detection(
        container=container,
        probe=explicit_unknown,
        manifest=detector,
    )

    assert second.evidence_json == first.evidence_json
    assert second.evidence_sha256 == first.evidence_sha256
    assert first.evidence_sha256 == hashlib.sha256(first.evidence_json).hexdigest()


@pytest.mark.parametrize("probe_id", ["0x8", None, "not-hex", "0x0"])
def test_track_mismatch_and_invalid_ids_classify_as_unknown(tmp_path, probe_id):
    result = classify_detection(
        container=container_signal(
            "recognized_logs",
            source_profile="apple-log-1",
            track_id=7,
            track_resolution="matched",
            signal_kind="apple-log-1-logs",
        ),
        probe=parse_probe_signal(
            json.dumps({"streams": [{"index": 0, "id": probe_id}]}).encode()
        ),
        manifest=manifest(tmp_path),
    )

    assert result.status == "unknown"
    assert result.source_profile is None


@pytest.mark.parametrize(
    ("color_fields", "expected_status"),
    [
        (
            {
                "color_primaries": "bt709",
                "color_transfer": "bt709",
                "color_space": "bt709",
            },
            "not_log",
        ),
        (
            {"color_transfer": "bt709", "color_space": "bt709"},
            "unknown",
        ),
        (
            {
                "color_primaries": "bt2020",
                "color_transfer": "bt709",
                "color_space": "bt2020nc",
            },
            "unknown",
        ),
    ],
)
def test_no_logs_triple_bt709_incomplete_and_non709_classification(
    tmp_path,
    color_fields,
    expected_status,
):
    result = classify_detection(
        container=container_signal(
            "no_logs",
            track_id=7,
            track_resolution="matched",
            signal_kind="no-logs",
        ),
        probe=parse_probe_signal(
            json.dumps(
                {"streams": [{"index": 0, "id": "0x7", **color_fields}]}
            ).encode()
        ),
        manifest=manifest(tmp_path),
    )

    assert result.status == expected_status
    assert result.source_profile is None


@pytest.mark.parametrize(
    "raw",
    [
        b'{"streams":[],"streams":[]}',
        b"[]",
        b'{"streams":NaN}',
        b"\xff",
    ],
)
def test_malformed_probe_output_is_terminal_not_unknown(raw):
    with pytest.raises(BoundedProcessError) as raised:
        parse_probe_signal(raw)

    assert raised.value.code == "log_probe_output_invalid"


def test_detection_serializer_never_exposes_raw_evidence(tmp_path):
    detector = manifest(tmp_path)
    result = classify_detection(
        container=container_signal(
            "recognized_logs",
            source_profile="apple-log-1",
            track_id=7,
            track_resolution="matched",
            signal_kind="apple-log-1-logs",
        ),
        probe=parse_probe_signal(b'{"streams":[{"index":0,"id":"0x7"}]}'),
        manifest=detector,
    )

    serialized = serialize_detection_identity(result, manifest=detector)

    assert serialized["detection_status"] == "apple_log"
    assert serialized["detector_evidence_sha256"] == result.evidence_sha256
    assert "evidence_json" not in serialized
    assert b"Apple Log" not in json.dumps(serialized).encode()
