import hashlib
import json
import os
import signal
from pathlib import Path

import pytest

from app.services import detector_certification
from app.services.bounded_subprocess import BoundedProcessResult
from app.services.detector_certification import (
    SNAPSHOT_NAMESPACE_PATTERN,
    _ephemeral_manifest,
    _temporary_snapshot_namespace,
    certify_detector,
    run_certifier_probe,
    resolve_certification_fixtures,
    verify_fixture_media,
)
from app.services.apple_log_detector import ProbeSignal
from app.services.detector_inspection import InspectionResult, serialize_inspection
from app.services.detector_manifest import (
    DetectorValidationError,
    canonical_document,
    load_certificate_summary,
    load_detector_manifest,
    load_rule_input,
)
from app.services.detector_manifest import FFPROBE_PROBE_ARGUMENTS
from app.services.iso_bmff_log_parser import ContainerSignal
from app.services.synthetic_detector_fixture import (
    build_apple_log_1_synthetic_container,
)
from tests.detector_test_support import write_detector_artifacts


def _write_fixture_root(root):
    (root / "media").mkdir(parents=True)
    root.chmod(0o700)
    (root / "media" / "apple.mov").write_bytes(b"apple")
    (root / "ordinary.mov").write_bytes(b"ordinary")
    descriptor_path = root / "detector-certification-v2.json"
    descriptor_path.write_bytes(
        canonical_document(
            {
                "schema_version": 2,
                "fixtures": [
                    {
                        "evidence_class": "real-container",
                        "expected_detection_status": "apple_log",
                        "expected_sha256": hashlib.sha256(b"apple").hexdigest(),
                        "expected_source_profile": "apple-log-2",
                        "path": "media/apple.mov",
                        "provenance": "user-owned-local-recording",
                        "role": "apple-log-2",
                    },
                    {
                        "evidence_class": "real-container",
                        "expected_detection_status": "not_log",
                        "expected_sha256": hashlib.sha256(b"ordinary").hexdigest(),
                        "expected_source_profile": None,
                        "path": "ordinary.mov",
                        "provenance": "user-owned-local-recording",
                        "role": "ordinary",
                    },
                ],
            }
        )
    )
    descriptor_path.chmod(0o600)


def _inspection_bytes(role):
    if role == "ordinary":
        container = ContainerSignal(
            kind="no_logs",
            source_profile=None,
            track_id=7,
            track_resolution="matched",
            signal_kind="no-logs",
            box_headers=8,
            max_depth_seen=6,
            metadata_bytes_read=64,
        )
        probe = ProbeSignal(
            index=0,
            track_id_status="valid",
            track_id=7,
            codec_type="video",
            color_space="bt709",
            color_transfer="bt709",
            color_primaries="bt709",
        )
    else:
        profile = role
        track_id = 1 if role == "apple-log-1" else 7
        container = ContainerSignal(
            kind="recognized_logs",
            source_profile=profile,
            track_id=track_id,
            track_resolution="matched",
            signal_kind=f"{profile}-logs",
            box_headers=8,
            max_depth_seen=6,
            metadata_bytes_read=64,
        )
        probe = ProbeSignal(
            index=0,
            track_id_status="valid",
            track_id=track_id,
            codec_type="video",
            color_space=None,
            color_transfer=None,
            color_primaries=None,
        )
    return serialize_inspection(InspectionResult(container=container, probe=probe))


def test_certifier_uses_rule_input_v2_contract(tmp_path):
    rule_path, _rule, _manifest = write_detector_artifacts(tmp_path)
    from app.services.detector_manifest import load_rule_input

    rule = load_rule_input(rule_path)
    ephemeral = _ephemeral_manifest(rule, "ffprobe pinned-test")

    assert ephemeral.detector_id == "apple-log-v2"
    assert ephemeral.parser_contract_version == "iso-bmff-apple-log-v1"
    assert ephemeral.identifier_mappings == rule.identifier_mappings


def test_snapshot_namespace_uses_fixed_random_name_pattern():
    with _temporary_snapshot_namespace() as snapshot_root:
        assert SNAPSHOT_NAMESPACE_PATTERN.fullmatch(snapshot_root.name) is not None


def test_snapshot_namespace_has_exact_owner_only_mode():
    with _temporary_snapshot_namespace() as snapshot_root:
        assert snapshot_root.stat().st_mode & 0o777 == 0o700


def test_snapshot_namespace_is_removed_after_normal_completion():
    snapshot_root = None
    with _temporary_snapshot_namespace() as current:
        snapshot_root = current
        (current / "snapshot").write_bytes(b"content")
        (current / "snapshot").chmod(0o400)
        assert current.exists()

    assert snapshot_root is not None
    assert not snapshot_root.exists()


def test_snapshot_namespace_is_removed_after_handled_exception():
    snapshot_root = None
    with pytest.raises(DetectorValidationError):
        with _temporary_snapshot_namespace() as current:
            snapshot_root = current
            (current / "snapshot").write_bytes(b"content")
            (current / "snapshot").chmod(0o400)
            raise DetectorValidationError()

    assert snapshot_root is not None
    assert not snapshot_root.exists()


@pytest.mark.parametrize("interruption", [TimeoutError, KeyboardInterrupt])
def test_snapshot_namespace_is_removed_after_timeout_or_interruption(interruption):
    snapshot_root = None
    with pytest.raises(interruption):
        with _temporary_snapshot_namespace() as current:
            snapshot_root = current
            (current / "snapshot").write_bytes(b"content")
            (current / "snapshot").chmod(0o400)
            raise interruption()

    assert snapshot_root is not None
    assert not snapshot_root.exists()


def test_snapshot_namespace_is_removed_by_catchable_termination_handler():
    snapshot_root = None
    with pytest.raises(SystemExit) as raised:
        with _temporary_snapshot_namespace() as current:
            snapshot_root = current
            (current / "snapshot").write_bytes(b"content")
            (current / "snapshot").chmod(0o400)
            handler = signal.getsignal(signal.SIGTERM)
            assert callable(handler)
            handler(signal.SIGTERM, None)

    assert raised.value.code == 128 + signal.SIGTERM
    assert snapshot_root is not None
    assert not snapshot_root.exists()


def test_snapshot_file_is_created_exclusive_no_follow_with_owner_only_mode(
    tmp_path, monkeypatch
):
    fixture_root = tmp_path / "fixtures"
    _write_fixture_root(fixture_root)
    source = verify_fixture_media(resolve_certification_fixtures(fixture_root)[0])
    real_open = os.open
    destination_open = None

    def recording_open(path, flags, mode=0o777, **kwargs):
        nonlocal destination_open
        if flags & os.O_CREAT:
            destination_open = (flags, mode)
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(detector_certification.os, "open", recording_open)

    with _temporary_snapshot_namespace() as snapshot_root:
        detector_certification._snapshot_fixture_media(
            source, snapshot_root=snapshot_root
        )

    assert destination_open is not None
    flags, mode = destination_open
    assert flags & os.O_EXCL
    assert flags & getattr(os, "O_NOFOLLOW", 0)
    assert mode == 0o600


def test_snapshot_copies_whole_file_while_verifying_sha256(tmp_path):
    fixture_root = tmp_path / "fixtures"
    _write_fixture_root(fixture_root)
    source = verify_fixture_media(resolve_certification_fixtures(fixture_root)[0])

    with _temporary_snapshot_namespace() as snapshot_root:
        snapshot = detector_certification._snapshot_fixture_media(
            source, snapshot_root=snapshot_root
        )

        assert snapshot.media_path.read_bytes() == b"apple"
        assert snapshot.media_sha256 == hashlib.sha256(b"apple").hexdigest()
        assert snapshot.size_bytes == len(b"apple")


def test_completed_snapshot_is_read_only(tmp_path):
    fixture_root = tmp_path / "fixtures"
    _write_fixture_root(fixture_root)
    source = verify_fixture_media(resolve_certification_fixtures(fixture_root)[0])

    with _temporary_snapshot_namespace() as snapshot_root:
        snapshot = detector_certification._snapshot_fixture_media(
            source, snapshot_root=snapshot_root
        )

        assert snapshot.media_path.stat().st_mode & 0o777 == 0o400


def test_certification_generates_parseable_apple_log_1_synthetic_fixture(tmp_path):
    with _temporary_snapshot_namespace() as snapshot_root:
        fixture = detector_certification._create_synthetic_apple_log_1_fixture(
            snapshot_root
        )
        descriptor = os.open(fixture.media_path, os.O_RDONLY)
        try:
            from app.services.iso_bmff_log_parser import parse_apple_log_signal

            signal = parse_apple_log_signal(
                descriptor, fixture.size_bytes, selected_track_id=1
            )
        finally:
            os.close(descriptor)

        assert fixture.input.role == "apple-log-1"
        assert fixture.input.evidence_class == "synthetic-container"
        assert signal.kind == "recognized_logs"
        assert signal.source_profile == "apple-log-1"


def test_resolve_certification_fixtures_requires_absolute_external_root(tmp_path):
    _write_fixture_root(tmp_path)

    resolved = resolve_certification_fixtures(tmp_path)

    assert tuple(item.input.role for item in resolved) == ("apple-log-2", "ordinary")
    with pytest.raises(DetectorValidationError):
        resolve_certification_fixtures(tmp_path.relative_to(tmp_path.parent))


@pytest.mark.parametrize("invalid_scope", ["root", "descriptor"])
def test_certification_validates_fixture_security_before_media_open(
    tmp_path, monkeypatch, invalid_scope
):
    detector_root = tmp_path / "detector"
    rule_path, _rule, _manifest = write_detector_artifacts(detector_root)
    fixture_root = tmp_path / "fixtures"
    _write_fixture_root(fixture_root)
    if invalid_scope == "root":
        fixture_root.chmod(0o755)
    else:
        (fixture_root / "detector-certification-v2.json").chmod(0o644)

    media_opened = False

    def fail_if_snapshot_started(*_args, **_kwargs):
        nonlocal media_opened
        media_opened = True
        raise AssertionError("media opened before fixture security validation")

    monkeypatch.setattr(
        detector_certification, "_snapshot_fixture_media", fail_if_snapshot_started
    )

    with pytest.raises(DetectorValidationError):
        certify_detector(rule_input_path=rule_path, fixture_root=fixture_root)

    assert media_opened is False


def test_verify_fixture_media_streams_and_matches_declared_sha256(tmp_path):
    _write_fixture_root(tmp_path)
    resolved = resolve_certification_fixtures(tmp_path)

    verified = tuple(verify_fixture_media(item) for item in resolved)

    assert verified[0].media_sha256 == hashlib.sha256(b"apple").hexdigest()
    assert verified[1].size_bytes == len(b"ordinary")

    (tmp_path / "media" / "apple.mov").write_bytes(b"changed")
    with pytest.raises(DetectorValidationError):
        verify_fixture_media(resolved[0])


def test_certification_rejects_external_hash_before_snapshot(tmp_path, monkeypatch):
    detector_root = tmp_path / "detector"
    rule_path, _rule, _manifest = write_detector_artifacts(detector_root)
    fixture_root = tmp_path / "fixtures"
    _write_fixture_root(fixture_root)
    (fixture_root / "media" / "apple.mov").write_bytes(b"unexpected")
    snapshot_started = False

    def fail_if_snapshot_started(*_args, **_kwargs):
        nonlocal snapshot_started
        snapshot_started = True
        raise AssertionError("snapshot started before expected SHA-256 validation")

    monkeypatch.setattr(
        detector_certification, "_snapshot_fixture_media", fail_if_snapshot_started
    )

    with pytest.raises(DetectorValidationError):
        certify_detector(rule_input_path=rule_path, fixture_root=fixture_root)

    assert snapshot_started is False


def test_certification_probes_private_snapshots_when_source_is_replaced(
    tmp_path, monkeypatch
):
    detector_root = tmp_path / "detector"
    rule_path, _rule, _manifest = write_detector_artifacts(detector_root)
    (detector_root / "manifest.json").unlink()
    (detector_root / "certificate-summary.json").unlink()
    fixture_root = tmp_path / "fixtures"
    _write_fixture_root(fixture_root)
    original_apple_sha256 = hashlib.sha256(b"apple").hexdigest()
    snapshot_roots = []

    def fake_version(*, fixture_root):
        snapshot_roots.append(fixture_root)
        assert fixture_root != fixture_root_external
        assert fixture_root.stat().st_mode & 0o777 == 0o700
        return BoundedProcessResult(
            stdout=b"ffprobe pinned-test\n", stderr=b"", returncode=0
        )

    def fake_probe(*, fixture_root, fixture):
        snapshot_roots.append(fixture_root)
        (fixture_root_external / "media" / "apple.mov").write_bytes(b"changed")
        assert fixture.media_path.is_relative_to(fixture_root)
        expected = {
            "apple-log-1": build_apple_log_1_synthetic_container(track_id=1),
            "apple-log-2": b"apple",
            "ordinary": b"ordinary",
        }[fixture.input.role]
        assert fixture.media_path.read_bytes() == expected
        return BoundedProcessResult(
            stdout=_inspection_bytes(fixture.input.role),
            stderr=b"",
            returncode=0,
        )

    fixture_root_external = fixture_root
    monkeypatch.setattr(
        detector_certification, "run_certifier_version", fake_version
    )
    monkeypatch.setattr(detector_certification, "run_certifier_probe", fake_probe)

    certify_detector(rule_input_path=rule_path, fixture_root=fixture_root)

    manifest = json.loads(
        (detector_root / "manifest.json").read_bytes()
    )
    apple_fixture = next(
        item for item in manifest["fixtures"] if item["role"] == "apple-log-2"
    )
    assert apple_fixture["sha256"] == original_apple_sha256
    assert snapshot_roots
    assert all(not root.exists() for root in snapshot_roots)


def test_certifier_runner_uses_only_repository_compose_service(tmp_path, monkeypatch):
    _write_fixture_root(tmp_path)
    fixture = verify_fixture_media(resolve_certification_fixtures(tmp_path)[0])
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return BoundedProcessResult(stdout=b"{}", stderr=b"", returncode=0)

    monkeypatch.setattr(detector_certification, "run_bounded_process", fake_run)
    monkeypatch.setattr(
        detector_certification.uuid,
        "uuid4",
        lambda: type("Uuid", (), {"hex": "1" * 32})(),
    )

    run_certifier_probe(fixture_root=tmp_path, fixture=fixture)

    argv = captured["argv"]
    assert argv[:3] == ["docker", "compose", "--project-directory"]
    assert "--profile" in argv
    assert "detector-certifier" in argv
    assert "-v" not in argv
    assert captured["kwargs"]["env"]["DETECTOR_FIXTURE_ROOT"] == str(tmp_path)
    assert argv[-5:] == [
        "python",
        "-m",
        "scripts.inspect_detector_fixture",
        "--fixture",
        "/fixtures/media/apple.mov",
    ]
    assert "ffprobe" not in argv
    assert captured["kwargs"]["max_stdout_bytes"] == 4_096
    assert captured["kwargs"]["cleanup"] is not None


def test_host_certifier_uses_only_sanitized_inspector_result(tmp_path, monkeypatch):
    detector_root = tmp_path / "detector"
    rule_path, _rule, _manifest = write_detector_artifacts(detector_root)
    fixture_root = tmp_path / "fixtures"
    _write_fixture_root(fixture_root)
    inspected_roles = []

    monkeypatch.setattr(
        detector_certification,
        "run_certifier_version",
        lambda **_kwargs: BoundedProcessResult(
            stdout=b"ffprobe pinned-test\n", stderr=b"", returncode=0
        ),
    )

    def fake_probe(*, fixture, **_kwargs):
        inspected_roles.append(fixture.input.role)
        return BoundedProcessResult(
            stdout=_inspection_bytes(fixture.input.role),
            stderr=b"raw container details are ignored",
            returncode=0,
        )

    monkeypatch.setattr(detector_certification, "run_certifier_probe", fake_probe)

    certify_detector(rule_input_path=rule_path, fixture_root=fixture_root)

    assert inspected_roles == ["apple-log-1", "apple-log-2", "ordinary"]
    manifest_bytes = (detector_root / "manifest.json").read_bytes()
    assert b"raw container details" not in manifest_bytes
    assert b"track_id" not in manifest_bytes
    assert b"metadata_bytes_read" not in manifest_bytes


def test_certification_never_uses_host_parser_or_direct_container_ffprobe(
    tmp_path, monkeypatch
):
    detector_root = tmp_path / "detector"
    rule_path, _rule, _manifest = write_detector_artifacts(detector_root)
    fixture_root = tmp_path / "fixtures"
    _write_fixture_root(fixture_root)
    inspected_roles = []

    monkeypatch.setattr(
        detector_certification,
        "run_certifier_version",
        lambda **_kwargs: BoundedProcessResult(
            stdout=b"ffprobe pinned-test\n", stderr=b"", returncode=0
        ),
    )

    def inspector_only(*, fixture, **_kwargs):
        inspected_roles.append(fixture.input.role)
        return BoundedProcessResult(
            stdout=_inspection_bytes(fixture.input.role), stderr=b"", returncode=0
        )

    monkeypatch.setattr(detector_certification, "run_certifier_probe", inspector_only)

    certify_detector(rule_input_path=rule_path, fixture_root=fixture_root)

    assert inspected_roles == ["apple-log-1", "apple-log-2", "ordinary"]
    text = Path(detector_certification.__file__).read_text(encoding="utf-8")
    assert "parse_apple_log_signal" not in text
    assert "classify_probe_bytes" not in text
    assert "FFPROBE_PROBE_ARGUMENTS, container_path" not in text


def test_certification_is_deterministic_and_publishes_no_paths(tmp_path, monkeypatch):
    detector_root = tmp_path / "detector"
    rule_path, _rule, _manifest = write_detector_artifacts(detector_root)
    (detector_root / "manifest.json").unlink()
    (detector_root / "certificate-summary.json").unlink()
    fixture_root = tmp_path / "fixtures"
    _write_fixture_root(fixture_root)
    approved_rule_bytes = rule_path.read_bytes()

    monkeypatch.setattr(
        detector_certification,
        "run_certifier_version",
        lambda **_kwargs: BoundedProcessResult(
            stdout=b"ffprobe pinned-test\n", stderr=b"", returncode=0
        ),
    )

    def fake_probe(*, fixture, **_kwargs):
        return BoundedProcessResult(
            stdout=_inspection_bytes(fixture.input.role),
            stderr=b"",
            returncode=0,
        )

    monkeypatch.setattr(detector_certification, "run_certifier_probe", fake_probe)

    first = certify_detector(rule_input_path=rule_path, fixture_root=fixture_root)
    first_manifest = (detector_root / "manifest.json").read_bytes()
    first_summary = (detector_root / "certificate-summary.json").read_bytes()
    second = certify_detector(rule_input_path=rule_path, fixture_root=fixture_root)

    assert first == second
    assert rule_path.read_bytes() == approved_rule_bytes
    assert (detector_root / "manifest.json").read_bytes() == first_manifest
    assert (detector_root / "certificate-summary.json").read_bytes() == first_summary
    assert str(fixture_root).encode() not in first_manifest
    assert b"apple.mov" not in first_manifest

    loaded_rule = load_rule_input(rule_path)
    loaded_manifest = load_detector_manifest(
        detector_root / "manifest.json", rule_input=loaded_rule
    )
    loaded_summary = load_certificate_summary(
        detector_root / "certificate-summary.json",
        rule_input=loaded_rule,
        manifest=loaded_manifest,
    )
    assert loaded_summary.future_apple_log_1_transform_allowed is False


def test_missing_production_input_does_not_publish_artifacts(tmp_path):
    detector_root = tmp_path / "missing-detector"
    fixture_root = tmp_path / "fixtures"
    _write_fixture_root(fixture_root)

    with pytest.raises(DetectorValidationError):
        certify_detector(
            rule_input_path=detector_root / "detector-rule-input-v2.json",
            fixture_root=fixture_root,
        )

    assert not (detector_root / "manifest.json").exists()
    assert not (detector_root / "certificate-summary.json").exists()


def test_manifest_and_summary_are_fsynced_then_atomically_replaced(
    tmp_path, monkeypatch
):
    target = tmp_path / "artifacts"
    target.mkdir()
    replacements = []
    real_replace = os.replace

    def recording_replace(source, destination):
        source_path = Path(source)
        assert source_path.parent == target
        assert source_path.stat().st_mode & 0o777 == 0o600
        replacements.append((source_path.name, Path(destination).name))
        real_replace(source, destination)

    monkeypatch.setattr(detector_certification.os, "replace", recording_replace)

    detector_certification._publish_artifacts(
        target_directory=target,
        artifacts={"manifest.json": b"manifest", "certificate-summary.json": b"summary"},
    )

    assert (target / "manifest.json").read_bytes() == b"manifest"
    assert (target / "certificate-summary.json").read_bytes() == b"summary"
    assert [destination for _source, destination in replacements] == [
        "manifest.json",
        "certificate-summary.json",
    ]
    assert not list(target.glob(".*.tmp"))


@pytest.mark.parametrize("target", ["descriptor", "directory", "media"])
def test_resolve_certification_fixtures_rejects_symlinks(tmp_path, target):
    fixture_root = tmp_path / "fixtures"
    _write_fixture_root(fixture_root)
    if target == "descriptor":
        real = tmp_path / "real-descriptor.json"
        descriptor = fixture_root / "detector-certification-v2.json"
        os.replace(descriptor, real)
        descriptor.symlink_to(real)
    elif target == "directory":
        real = tmp_path / "real-media"
        os.replace(fixture_root / "media", real)
        (fixture_root / "media").symlink_to(real, target_is_directory=True)
    else:
        real = tmp_path / "real-apple.mov"
        os.replace(fixture_root / "media" / "apple.mov", real)
        (fixture_root / "media" / "apple.mov").symlink_to(real)

    with pytest.raises(DetectorValidationError):
        resolve_certification_fixtures(fixture_root)
