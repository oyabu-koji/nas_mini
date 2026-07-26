import hashlib
import json

import pytest

from app.services.detector_manifest import (
    DetectorValidationError,
    canonical_document,
    load_certificate_summary,
    load_detector_manifest,
    load_fixture_descriptor,
    load_rule_input,
)
from tests.detector_test_support import write_detector_artifacts


def test_rule_manifest_and_summary_validate_as_one_identity(tmp_path):
    rule_path, _rule, _manifest = write_detector_artifacts(tmp_path)

    rule_input = load_rule_input(rule_path)
    manifest = load_detector_manifest(tmp_path / "manifest.json", rule_input=rule_input)
    summary = load_certificate_summary(
        tmp_path / "certificate-summary.json",
        rule_input=rule_input,
        manifest=manifest,
    )

    assert rule_input.detector_id == "apple-log-v1"
    assert manifest.apple_log == rule_input.apple_log
    assert summary.manifest_sha256 == manifest.manifest_sha256


@pytest.mark.parametrize(
    "mutation",
    ["bom", "noncanonical", "unknown", "bad_path", "unapproved", "digest"],
)
def test_rule_input_rejects_noncanonical_or_unapproved_content(tmp_path, mutation):
    rule_path, rule, _manifest = write_detector_artifacts(tmp_path)
    if mutation == "bom":
        rule_path.write_bytes(b"\xef\xbb\xbf" + rule_path.read_bytes())
    elif mutation == "noncanonical":
        rule_path.write_text(json.dumps(rule), encoding="utf-8")
    elif mutation == "unknown":
        rule["unknown"] = True
        rule_path.write_bytes(canonical_document(rule))
    elif mutation == "bad_path":
        rule["apple_log"][0]["path"] = "format.filename"
        rule_path.write_bytes(canonical_document(rule))
    elif mutation == "unapproved":
        rule["approval"]["approved_at"] = "2026-07-24T12:00:00"
        rule_path.write_bytes(canonical_document(rule))
    else:
        rule_path.with_suffix(".sha256").write_text("0" * 64 + "\n", encoding="ascii")

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


def test_rule_input_rejects_duplicate_keys(tmp_path):
    rule_path, _rule, _manifest = write_detector_artifacts(tmp_path)
    rule_path.write_bytes(b'{"schema_version":1,"schema_version":1}')

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


def test_rule_input_preserves_ordered_all_of_and_present_operator(tmp_path):
    rule_path, rule, _manifest = write_detector_artifacts(tmp_path)
    present = {
        "path": "streams.0.tags.transfer_characteristic",
        "operator": "present",
        "expected_value": None,
        "rationale": "presence is required by the approved test rule",
        "source_reference": "https://example.invalid/technical-reference",
    }
    rule["apple_log"].insert(0, present)
    rule_bytes = canonical_document(rule)
    rule_path.write_bytes(rule_bytes)
    rule_path.with_suffix(".sha256").write_text(
        hashlib.sha256(rule_bytes).hexdigest() + "\n", encoding="ascii"
    )

    loaded = load_rule_input(rule_path)

    assert [predicate.operator for predicate in loaded.apple_log] == ["present", "equals"]


@pytest.mark.parametrize(
    ("operator", "expected"),
    [("present", "value"), ("equals", None), ("regex", "Apple.*")],
)
def test_rule_input_rejects_unsupported_operator_contract(tmp_path, operator, expected):
    rule_path, rule, _manifest = write_detector_artifacts(tmp_path)
    rule["apple_log"][0]["operator"] = operator
    rule["apple_log"][0]["expected_value"] = expected
    rule_bytes = canonical_document(rule)
    rule_path.write_bytes(rule_bytes)
    rule_path.with_suffix(".sha256").write_text(
        hashlib.sha256(rule_bytes).hexdigest() + "\n", encoding="ascii"
    )

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


@pytest.mark.parametrize(
    "path",
    [
        "format.tags.com.apple.quicktime.camera.identifier",
        "streams.0.tags.transfer_characteristic",
        "streams.0.disposition.default",
        "streams.0.color_space",
        "streams.0.color_transfer",
        "streams.0.color_primaries",
        "streams.0.codec_type",
    ],
)
def test_rule_input_accepts_only_allowlisted_probe_metadata_paths(tmp_path, path):
    rule_path, rule, _manifest = write_detector_artifacts(tmp_path)
    rule["apple_log"][0]["path"] = path
    rule_bytes = canonical_document(rule)
    rule_path.write_bytes(rule_bytes)
    rule_path.with_suffix(".sha256").write_text(
        hashlib.sha256(rule_bytes).hexdigest() + "\n", encoding="ascii"
    )

    loaded = load_rule_input(rule_path)

    assert loaded.apple_log[0].path == path


@pytest.mark.parametrize(
    "path",
    [
        "streams.*.tags.transfer_characteristic",
        "streams.[0-9]+.color_transfer",
        "streams.0.tags[?(@.name)]",
        "format.filename",
        "format.is_log",
        "streams.0.pix_fmt",
        "streams.1.color_transfer",
    ],
)
def test_rule_input_rejects_wildcard_expression_and_nonmetadata_paths(tmp_path, path):
    rule_path, rule, _manifest = write_detector_artifacts(tmp_path)
    rule["apple_log"][0]["path"] = path
    rule_bytes = canonical_document(rule)
    rule_path.write_bytes(rule_bytes)
    rule_path.with_suffix(".sha256").write_text(
        hashlib.sha256(rule_bytes).hexdigest() + "\n", encoding="ascii"
    )

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("approval", "approving_role"),
        ("approval", "approval_reference"),
        ("predicate", "rationale"),
        ("predicate", "source_reference"),
    ],
)
def test_rule_input_requires_approval_and_predicate_references(tmp_path, section, field):
    rule_path, rule, _manifest = write_detector_artifacts(tmp_path)
    target = rule["approval"] if section == "approval" else rule["apple_log"][0]
    target[field] = ""
    rule_bytes = canonical_document(rule)
    rule_path.write_bytes(rule_bytes)
    rule_path.with_suffix(".sha256").write_text(
        hashlib.sha256(rule_bytes).hexdigest() + "\n", encoding="ascii"
    )

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


def test_manifest_rejects_rule_change(tmp_path):
    rule_path, _rule, manifest_value = write_detector_artifacts(tmp_path)
    rule_input = load_rule_input(rule_path)
    manifest_value["rules"]["apple_log"][0]["expected_value"] = "changed"
    from app.services.detector_manifest import document_with_digest

    (tmp_path / "manifest.json").write_bytes(
        document_with_digest(manifest_value, "manifest_sha256")
    )

    with pytest.raises(DetectorValidationError):
        load_detector_manifest(tmp_path / "manifest.json", rule_input=rule_input)


def test_manifest_rejects_python_equal_but_byte_different_rule(tmp_path):
    rule_path, rule, _manifest_value = write_detector_artifacts(tmp_path)
    rule["apple_log"][0]["expected_value"] = 1
    rule_bytes = canonical_document(rule)
    rule_path.write_bytes(rule_bytes)
    rule_path.with_suffix(".sha256").write_text(
        hashlib.sha256(rule_bytes).hexdigest() + "\n", encoding="ascii"
    )
    rule_input = load_rule_input(rule_path)

    _, _, manifest_value = write_detector_artifacts(tmp_path)
    manifest_value["rule_input_sha256"] = rule_input.sha256
    manifest_value["rules"]["apple_log"][0]["expected_value"] = True
    from app.services.detector_manifest import document_with_digest

    (tmp_path / "manifest.json").write_bytes(
        document_with_digest(manifest_value, "manifest_sha256")
    )

    with pytest.raises(DetectorValidationError):
        load_detector_manifest(tmp_path / "manifest.json", rule_input=rule_input)


@pytest.mark.parametrize("mutation", ["bom", "noncanonical", "unknown", "duplicate", "digest"])
def test_manifest_rejects_noncanonical_or_untrusted_identity(tmp_path, mutation):
    rule_path, _rule, manifest_value = write_detector_artifacts(tmp_path)
    rule_input = load_rule_input(rule_path)
    manifest_path = tmp_path / "manifest.json"
    if mutation == "bom":
        manifest_path.write_bytes(b"\xef\xbb\xbf" + manifest_path.read_bytes())
    elif mutation == "noncanonical":
        manifest_path.write_text(
            json.dumps(json.loads(manifest_path.read_bytes())), encoding="utf-8"
        )
    elif mutation == "unknown":
        manifest_value["unknown"] = True
        from app.services.detector_manifest import document_with_digest

        manifest_path.write_bytes(document_with_digest(manifest_value, "manifest_sha256"))
    elif mutation == "duplicate":
        manifest_path.write_bytes(b'{"schema_version":1,"schema_version":1}')
    else:
        decoded = json.loads(manifest_path.read_bytes())
        decoded["manifest_sha256"] = "0" * 64
        manifest_path.write_bytes(canonical_document(decoded))

    with pytest.raises(DetectorValidationError):
        load_detector_manifest(manifest_path, rule_input=rule_input)


def test_fixture_descriptor_has_exact_immutable_roles(tmp_path):
    descriptor_path = tmp_path / "fixture-input-v1.json"
    descriptor_path.write_bytes(
        canonical_document(
            {
                "schema_version": 1,
                "fixtures": [
                    {
                        "role": "apple_log",
                        "relative_media_path": "apple-log.mov",
                        "expected_media_sha256": "a" * 64,
                        "expected_classification": "apple_log",
                        "source_label": "user-owned-local-recording",
                    },
                    {
                        "role": "ordinary",
                        "relative_media_path": "ordinary.mov",
                        "expected_media_sha256": "b" * 64,
                        "expected_classification": "not_log",
                        "source_label": "user-owned-local-recording",
                    },
                ],
            }
        )
    )

    descriptor = load_fixture_descriptor(descriptor_path)

    assert tuple(item.role for item in descriptor.fixtures) == ("apple_log", "ordinary")
    with pytest.raises((AttributeError, TypeError)):
        descriptor.fixtures[0].role = "ordinary"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("relative_media_path", "../outside.mov"),
        ("expected_media_sha256", "A" * 64),
        ("expected_classification", "unknown"),
        ("source_label", "downloaded"),
    ],
)
def test_fixture_descriptor_rejects_unsafe_or_mutable_identity(tmp_path, field, value):
    fixture = {
        "role": "apple_log",
        "relative_media_path": "apple-log.mov",
        "expected_media_sha256": "a" * 64,
        "expected_classification": "apple_log",
        "source_label": "user-owned-local-recording",
    }
    fixture[field] = value
    descriptor_path = tmp_path / "fixture-input-v1.json"
    descriptor_path.write_bytes(
        canonical_document(
            {
                "schema_version": 1,
                "fixtures": [
                    fixture,
                    {
                        "role": "ordinary",
                        "relative_media_path": "ordinary.mov",
                        "expected_media_sha256": "b" * 64,
                        "expected_classification": "not_log",
                        "source_label": "user-owned-local-recording",
                    },
                ],
            }
        )
    )

    with pytest.raises(DetectorValidationError):
        load_fixture_descriptor(descriptor_path)
