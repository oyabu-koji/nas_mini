import json
import os
from typing import get_args

import pytest

from app.services.detector_fixture_descriptor import (
    FIXTURE_EVIDENCE_CLASSES,
    FIXTURE_ROLES,
    FixtureEvidenceClass,
    FixtureProvenance,
    FixtureRole,
    LOCAL_DESCRIPTOR_FIELDS,
    LOCAL_DESCRIPTOR_REPOSITORY_PATH,
    LOCAL_DESCRIPTOR_SCHEMA_VERSION,
    LOCAL_FIXTURE_FIELDS,
    LOCAL_FIXTURE_PROVENANCE,
    confine_fixture_path,
    load_local_fixture_descriptor,
    validate_descriptor_file,
    validate_relative_fixture_path,
    validate_fixture_root,
)
from app.services.detector_manifest import DetectorValidationError


def _descriptor_value():
    return {
        "schema_version": 2,
        "fixtures": [
            {
                "evidence_class": "real-container",
                "expected_detection_status": "apple_log",
                "expected_sha256": "a" * 64,
                "expected_source_profile": "apple-log-2",
                "path": "A001.mov",
                "provenance": "user-owned-local-recording",
                "role": "apple-log-2",
            },
            {
                "evidence_class": "real-container",
                "expected_detection_status": "not_log",
                "expected_sha256": "b" * 64,
                "expected_source_profile": None,
                "path": "ordinary.mov",
                "provenance": "user-owned-local-recording",
                "role": "ordinary",
            },
        ],
    }


def _write_descriptor(path, value):
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    os.chmod(path, 0o600)


def test_detector_v2_local_descriptor_schema_is_exact():
    assert LOCAL_DESCRIPTOR_SCHEMA_VERSION == 2
    assert LOCAL_DESCRIPTOR_FIELDS == {"schema_version", "fixtures"}
    assert LOCAL_FIXTURE_FIELDS == {
        "evidence_class",
        "expected_detection_status",
        "expected_sha256",
        "expected_source_profile",
        "path",
        "provenance",
        "role",
    }


def test_detector_v2_local_descriptor_path_is_fixed():
    assert str(LOCAL_DESCRIPTOR_REPOSITORY_PATH) == (
        "data/detector-certification-v2.json"
    )


def test_detector_v2_fixture_role_is_closed():
    assert set(get_args(FixtureRole)) == FIXTURE_ROLES == {
        "apple-log-2",
        "ordinary",
    }


def test_detector_v2_fixture_evidence_class_is_closed():
    assert set(get_args(FixtureEvidenceClass)) == FIXTURE_EVIDENCE_CLASSES == {
        "real-container"
    }


def test_detector_v2_fixture_provenance_is_fixed():
    assert get_args(FixtureProvenance) == (LOCAL_FIXTURE_PROVENANCE,)
    assert LOCAL_FIXTURE_PROVENANCE == "user-owned-local-recording"


@pytest.mark.parametrize(
    "value",
    [
        "/absolute.mov",
        "../outside.mov",
        "nested/../../outside.mov",
        "nested//file.mov",
        "nested/./file.mov",
        "nested\\file.mov",
        "",
    ],
)
def test_detector_v2_fixture_path_requires_strict_relative_posix_path(value):
    with pytest.raises(DetectorValidationError):
        validate_relative_fixture_path(value)

    assert str(validate_relative_fixture_path("nested/file.mov")) == "nested/file.mov"


def test_detector_v2_fixture_path_is_confined_below_fixture_root(tmp_path):
    fixture_root = tmp_path / "data"
    fixture_root.mkdir()
    nested = fixture_root / "nested"
    nested.mkdir()
    fixture = nested / "file.mov"
    fixture.write_bytes(b"fixture")

    assert confine_fixture_path(fixture_root, "nested/file.mov") == fixture

    outside = tmp_path / "outside.mov"
    outside.write_bytes(b"outside")
    (fixture_root / "escape.mov").symlink_to(outside)
    with pytest.raises(DetectorValidationError):
        confine_fixture_path(fixture_root, "escape.mov")


def test_detector_v2_fixture_root_requires_owner_directory_no_symlink_and_mode(
    tmp_path, monkeypatch
):
    fixture_root = tmp_path / "data"
    fixture_root.mkdir(mode=0o700)
    os.chmod(fixture_root, 0o700)

    validate_fixture_root(fixture_root)

    os.chmod(fixture_root, 0o755)
    with pytest.raises(DetectorValidationError):
        validate_fixture_root(fixture_root)
    os.chmod(fixture_root, 0o700)

    monkeypatch.setattr(os, "getuid", lambda: fixture_root.stat().st_uid + 1)
    with pytest.raises(DetectorValidationError):
        validate_fixture_root(fixture_root)

    symlink = tmp_path / "data-link"
    symlink.symlink_to(fixture_root, target_is_directory=True)
    with pytest.raises(DetectorValidationError):
        validate_fixture_root(symlink)

    regular_file = tmp_path / "not-a-directory"
    regular_file.write_bytes(b"")
    with pytest.raises(DetectorValidationError):
        validate_fixture_root(regular_file)


def test_detector_v2_descriptor_requires_owner_regular_no_symlink_and_mode(
    tmp_path, monkeypatch
):
    descriptor = tmp_path / "detector-certification-v2.json"
    descriptor.write_bytes(b"{}")
    os.chmod(descriptor, 0o600)

    validate_descriptor_file(descriptor)

    os.chmod(descriptor, 0o644)
    with pytest.raises(DetectorValidationError):
        validate_descriptor_file(descriptor)
    os.chmod(descriptor, 0o600)

    actual_uid = descriptor.stat().st_uid
    monkeypatch.setattr(os, "getuid", lambda: actual_uid + 1)
    with pytest.raises(DetectorValidationError):
        validate_descriptor_file(descriptor)
    monkeypatch.setattr(os, "getuid", lambda: actual_uid)

    symlink = tmp_path / "descriptor-link.json"
    symlink.symlink_to(descriptor)
    with pytest.raises(DetectorValidationError):
        validate_descriptor_file(symlink)

    directory = tmp_path / "descriptor-directory"
    directory.mkdir(mode=0o700)
    with pytest.raises(DetectorValidationError):
        validate_descriptor_file(directory)


@pytest.mark.parametrize("scope", ["top-level", "fixture"])
def test_detector_v2_descriptor_rejects_unknown_fields(tmp_path, scope):
    value = _descriptor_value()
    target = value if scope == "top-level" else value["fixtures"][0]
    target["unknown"] = "forbidden"
    descriptor = tmp_path / "detector-certification-v2.json"
    _write_descriptor(descriptor, value)

    with pytest.raises(DetectorValidationError):
        load_local_fixture_descriptor(descriptor)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":2,"schema_version":2,"fixtures":[]}',
        b'{"schema_version":2,"fixtures":[{"role":"ordinary","role":"ordinary"}]}',
    ],
)
def test_detector_v2_descriptor_rejects_duplicate_keys(tmp_path, raw):
    descriptor = tmp_path / "detector-certification-v2.json"
    descriptor.write_bytes(raw)
    os.chmod(descriptor, 0o600)

    with pytest.raises(DetectorValidationError):
        load_local_fixture_descriptor(descriptor)


@pytest.mark.parametrize("mutation", ["absolute-path", "raw-metadata"])
def test_detector_v2_descriptor_schema_cannot_record_paths_or_raw_metadata(
    tmp_path, mutation
):
    value = _descriptor_value()
    if mutation == "absolute-path":
        value["fixtures"][0]["path"] = "/private/user/recording.mov"
    else:
        value["fixtures"][0]["raw_metadata"] = {
            "camera": "private",
            "ffprobe": {"streams": []},
        }
    descriptor = tmp_path / "detector-certification-v2.json"
    _write_descriptor(descriptor, value)

    with pytest.raises(DetectorValidationError):
        load_local_fixture_descriptor(descriptor)
