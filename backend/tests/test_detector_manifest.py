import hashlib
import json

import pytest

from app.services.detector_manifest import (
    DetectorValidationError,
    FFPROBE_SHOW_ENTRIES,
    canonical_document,
    load_certificate_summary,
    load_detector_manifest,
    load_fixture_descriptor,
    load_rule_input,
)
from tests.detector_test_support import write_detector_artifacts


def test_rule_input_v2_uses_a_strict_closed_schema(tmp_path):
    rule_path, rule, _manifest = write_detector_artifacts(tmp_path)

    loaded = load_rule_input(rule_path)

    assert loaded.detector_id == "apple-log-v2"
    assert loaded.parser_contract_version == "iso-bmff-apple-log-v1"
    assert tuple(item.source_profile for item in loaded.identifier_mappings) == (
        "apple-log-1",
        "apple-log-2",
    )

    rule["unexpected"] = True
    raw = canonical_document(rule)
    rule_path.write_bytes(raw)
    rule_path.with_suffix(".sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "\n",
        encoding="ascii",
    )

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


def test_rule_parser_contract_version_matches_code(tmp_path):
    rule_path, rule, _manifest = write_detector_artifacts(tmp_path)

    assert load_rule_input(rule_path).parser_contract_version == (
        "iso-bmff-apple-log-v1"
    )

    rule["parser_contract_version"] = "iso-bmff-apple-log-v0"
    raw = canonical_document(rule)
    rule_path.write_bytes(raw)
    rule_path.with_suffix(".sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "\n",
        encoding="ascii",
    )

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


@pytest.mark.parametrize("mutation", ["identifier", "profile", "order"])
def test_rule_identifier_to_profile_mapping_is_exact(tmp_path, mutation):
    rule_path, rule, _manifest = write_detector_artifacts(tmp_path)
    if mutation == "identifier":
        rule["identifier_mappings"][0]["identifier"] += ".suffix"
    elif mutation == "profile":
        rule["identifier_mappings"][0]["source_profile"] = "apple-log-2"
    else:
        rule["identifier_mappings"].reverse()
    raw = canonical_document(rule)
    rule_path.write_bytes(raw)
    rule_path.with_suffix(".sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "\n",
        encoding="ascii",
    )

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


@pytest.mark.parametrize("mutation", ["preset", "cross_profile", "order"])
def test_rule_profile_to_requested_preset_mapping_is_exact(tmp_path, mutation):
    rule_path, rule, _manifest = write_detector_artifacts(tmp_path)
    if mutation == "preset":
        rule["profile_preset_mappings"][1]["requested_preset_id"] = (
            "generated-apple-log-rec709"
        )
    elif mutation == "cross_profile":
        rule["profile_preset_mappings"][0]["source_profile"] = "apple-log-2"
    else:
        rule["profile_preset_mappings"].reverse()
    raw = canonical_document(rule)
    rule_path.write_bytes(raw)
    rule_path.with_suffix(".sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "\n",
        encoding="ascii",
    )

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


@pytest.mark.parametrize("mutation", ["extra_value", "profile", "order"])
def test_rule_profile_color_allowlists_are_exact(tmp_path, mutation):
    rule_path, rule, _manifest = write_detector_artifacts(tmp_path)
    if mutation == "extra_value":
        rule["color_allowlists"][1]["color_primaries"].append("bt2020")
    elif mutation == "profile":
        rule["color_allowlists"][0]["source_profile"] = "apple-log-2"
    else:
        rule["color_allowlists"].reverse()
    raw = canonical_document(rule)
    rule_path.write_bytes(raw)
    rule_path.with_suffix(".sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "\n",
        encoding="ascii",
    )

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


@pytest.mark.parametrize(
    "field",
    ["color_primaries", "color_transfer", "color_space"],
)
def test_rule_not_log_predicate_requires_exact_triple_bt709(tmp_path, field):
    rule_path, rule, _manifest = write_detector_artifacts(tmp_path)
    rule["not_log_predicate"][field] = "unknown"
    raw = canonical_document(rule)
    rule_path.write_bytes(raw)
    rule_path.with_suffix(".sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "\n",
        encoding="ascii",
    )

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


@pytest.mark.parametrize(
    "mutation",
    ["official_source", "identifier_source", "role", "time", "reference"],
)
def test_rule_requires_official_source_and_complete_approval(tmp_path, mutation):
    rule_path, rule, _manifest = write_detector_artifacts(tmp_path)
    if mutation == "official_source":
        rule["official_source_url"] = "https://example.invalid/not-apple"
    elif mutation == "identifier_source":
        rule["identifier_mappings"][0]["source_reference"] = (
            "https://example.invalid/not-apple"
        )
    elif mutation == "role":
        rule["approval"]["approving_role"] = ""
    elif mutation == "time":
        rule["approval"]["approved_at"] = "2026-08-12T00:00:00"
    else:
        rule["approval"]["approval_reference"] = ""
    raw = canonical_document(rule)
    rule_path.write_bytes(raw)
    rule_path.with_suffix(".sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "\n",
        encoding="ascii",
    )

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


@pytest.mark.parametrize(
    "field",
    [
        "file_size_bytes",
        "box_headers",
        "nesting_depth",
        "video_tracks",
        "sample_descriptions",
        "metadata_bytes",
        "retained_identifiers",
    ],
)
def test_rule_resource_limits_must_match_parser_code(tmp_path, field):
    rule_path, rule, _manifest = write_detector_artifacts(tmp_path)
    rule["resource_limits"][field] += 1
    raw = canonical_document(rule)
    rule_path.write_bytes(raw)
    rule_path.with_suffix(".sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "\n",
        encoding="ascii",
    )

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


def test_rule_v2_uses_lowercase_canonical_json_sha256_sidecar(tmp_path):
    rule_path, _rule, _manifest = write_detector_artifacts(tmp_path)
    sidecar = rule_path.parent / f"{rule_path.name}.sha256"

    loaded = load_rule_input(rule_path)

    assert sidecar.name == "detector-rule-input-v2.json.sha256"
    assert sidecar.read_text(encoding="ascii").strip() == loaded.sha256

    sidecar.write_text(loaded.sha256.upper() + "\n", encoding="ascii")
    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


def test_manifest_v2_uses_strict_data_class_and_validator(tmp_path):
    rule_path, _rule, _manifest = write_detector_artifacts(tmp_path)

    rule_input = load_rule_input(rule_path)
    manifest = load_detector_manifest(tmp_path / "manifest.json", rule_input=rule_input)

    assert manifest.detector_id == "apple-log-v2"
    assert manifest.parser_contract_version == rule_input.parser_contract_version
    assert manifest.identifier_mappings == rule_input.identifier_mappings
    assert tuple(item.role for item in manifest.fixtures) == (
        "apple-log-1",
        "apple-log-2",
        "ordinary",
    )


def test_certificate_summary_v2_matches_manifest_and_blocks_future_log1_transform(
    tmp_path,
):
    rule_path, _rule, _manifest = write_detector_artifacts(tmp_path)
    rule = load_rule_input(rule_path)
    manifest = load_detector_manifest(tmp_path / "manifest.json", rule_input=rule)

    summary = load_certificate_summary(
        tmp_path / "certificate-summary.json",
        rule_input=rule,
        manifest=manifest,
    )

    assert summary.parser_contract_version == "iso-bmff-apple-log-v1"
    assert summary.future_apple_log_1_transform_allowed is False
    assert tuple(item.role for item in summary.fixtures) == (
        "apple-log-1",
        "apple-log-2",
        "ordinary",
    )


def test_certificate_summary_v2_rejects_future_log1_transform_permission(tmp_path):
    rule_path, _rule, _manifest = write_detector_artifacts(tmp_path)
    rule = load_rule_input(rule_path)
    manifest = load_detector_manifest(tmp_path / "manifest.json", rule_input=rule)
    summary_path = tmp_path / "certificate-summary.json"
    value = json.loads(summary_path.read_bytes())
    value["future_apple_log_1_transform_allowed"] = True
    summary_path.write_bytes(canonical_document(value))

    with pytest.raises(DetectorValidationError):
        load_certificate_summary(summary_path, rule_input=rule, manifest=manifest)


def test_manifest_pins_parser_contract_version(tmp_path):
    rule_path, _rule, manifest_value = write_detector_artifacts(tmp_path)
    rule_input = load_rule_input(rule_path)
    manifest_value["parser_contract_version"] = "iso-bmff-apple-log-v0"
    from app.services.detector_manifest import document_with_digest

    (tmp_path / "manifest.json").write_bytes(
        document_with_digest(manifest_value, "manifest_sha256")
    )

    with pytest.raises(DetectorValidationError):
        load_detector_manifest(tmp_path / "manifest.json", rule_input=rule_input)


@pytest.mark.parametrize("mutation", ["show_entries", "ffprobe_version"])
def test_manifest_pins_exact_ffprobe_version_and_show_entries(tmp_path, mutation):
    rule_path, _rule, manifest_value = write_detector_artifacts(tmp_path)
    rule_input = load_rule_input(rule_path)
    if mutation == "show_entries":
        manifest_value["show_entries"] += ":format_tags"
    else:
        manifest_value["ffprobe_version"] = ""
    from app.services.detector_manifest import document_with_digest

    (tmp_path / "manifest.json").write_bytes(
        document_with_digest(manifest_value, "manifest_sha256")
    )

    with pytest.raises(DetectorValidationError):
        load_detector_manifest(tmp_path / "manifest.json", rule_input=rule_input)


def test_ffprobe_show_entries_excludes_tags_and_disposition():
    assert FFPROBE_SHOW_ENTRIES == (
        "stream=index,id,codec_type,color_space,color_transfer,color_primaries"
    )
    assert "tags" not in FFPROBE_SHOW_ENTRIES
    assert "disposition" not in FFPROBE_SHOW_ENTRIES
    assert "format" not in FFPROBE_SHOW_ENTRIES


@pytest.mark.parametrize(
    "mutation",
    ["bom", "noncanonical", "unknown", "bad_identifier", "unapproved", "digest"],
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
    elif mutation == "bad_identifier":
        rule["identifier_mappings"][0]["identifier"] = "not-ascii-\u2603"
        rule_path.write_bytes(canonical_document(rule))
    elif mutation == "unapproved":
        rule["approval"]["approved_at"] = "2026-07-24T12:00:00"
        rule_path.write_bytes(canonical_document(rule))
    else:
        (rule_path.parent / f"{rule_path.name}.sha256").write_text(
            "0" * 64 + "\n",
            encoding="ascii",
        )

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


def test_rule_input_rejects_duplicate_keys(tmp_path):
    rule_path, _rule, _manifest = write_detector_artifacts(tmp_path)
    rule_path.write_bytes(b'{"schema_version":2,"schema_version":2}')

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
    target = (
        rule["approval"]
        if section == "approval"
        else rule["identifier_mappings"][0]
    )
    target[field] = ""
    rule_bytes = canonical_document(rule)
    rule_path.write_bytes(rule_bytes)
    (rule_path.parent / f"{rule_path.name}.sha256").write_text(
        hashlib.sha256(rule_bytes).hexdigest() + "\n", encoding="ascii"
    )

    with pytest.raises(DetectorValidationError):
        load_rule_input(rule_path)


def test_manifest_rejects_rule_change(tmp_path):
    rule_path, _rule, manifest_value = write_detector_artifacts(tmp_path)
    rule_input = load_rule_input(rule_path)
    manifest_value["identifier_mappings"][0]["identifier"] += ".changed"
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
        manifest_path.write_bytes(b'{"schema_version":2,"schema_version":2}')
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
