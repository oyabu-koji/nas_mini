import hashlib
import json
import os

import pytest

from app.services import detector_certification
from app.services.bounded_subprocess import BoundedProcessResult
from app.services.detector_certification import (
    certify_detector,
    run_certifier_probe,
    resolve_certification_fixtures,
    verify_fixture_media,
)
from app.services.detector_manifest import FFPROBE_PROBE_ARGUMENTS
from tests.detector_test_support import write_detector_artifacts
from app.services.detector_manifest import DetectorValidationError, canonical_document


def _write_fixture_root(root):
    (root / "media").mkdir(parents=True)
    (root / "media" / "apple.mov").write_bytes(b"apple")
    (root / "ordinary.mov").write_bytes(b"ordinary")
    (root / "fixture-input-v1.json").write_bytes(
        canonical_document(
            {
                "schema_version": 1,
                "fixtures": [
                    {
                        "role": "apple_log",
                        "relative_media_path": "media/apple.mov",
                        "expected_media_sha256": hashlib.sha256(b"apple").hexdigest(),
                        "expected_classification": "apple_log",
                        "source_label": "user-owned-local-recording",
                    },
                    {
                        "role": "ordinary",
                        "relative_media_path": "ordinary.mov",
                        "expected_media_sha256": hashlib.sha256(b"ordinary").hexdigest(),
                        "expected_classification": "not_log",
                        "source_label": "user-owned-local-recording",
                    },
                ],
            }
        )
    )


def test_resolve_certification_fixtures_requires_absolute_external_root(tmp_path):
    _write_fixture_root(tmp_path)

    resolved = resolve_certification_fixtures(tmp_path)

    assert tuple(item.input.role for item in resolved) == ("apple_log", "ordinary")
    with pytest.raises(DetectorValidationError):
        resolve_certification_fixtures(tmp_path.relative_to(tmp_path.parent))


def test_verify_fixture_media_streams_and_matches_declared_sha256(tmp_path):
    _write_fixture_root(tmp_path)
    resolved = resolve_certification_fixtures(tmp_path)

    verified = tuple(verify_fixture_media(item) for item in resolved)

    assert verified[0].media_sha256 == hashlib.sha256(b"apple").hexdigest()
    assert verified[1].size_bytes == len(b"ordinary")

    (tmp_path / "media" / "apple.mov").write_bytes(b"changed")
    with pytest.raises(DetectorValidationError):
        verify_fixture_media(resolved[0])


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
        expected = b"apple" if fixture.input.role == "apple_log" else b"ordinary"
        assert fixture.media_path.read_bytes() == expected
        raw = (
            b'{"streams":[{"tags":{"transfer_characteristic":"Apple Log"}}]}'
            if fixture.input.role == "apple_log"
            else b'{"streams":[{"color_transfer":"bt709","tags":{}}]}'
        )
        return BoundedProcessResult(stdout=raw, stderr=b"", returncode=0)

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
        item for item in manifest["fixtures"] if item["role"] == "apple_log"
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
    assert "ffprobe" in argv
    assert [*FFPROBE_PROBE_ARGUMENTS, "/fixtures/media/apple.mov"] == argv[-9:]
    assert "ffprobe" not in argv[: argv.index("detector-certifier")]
    assert captured["kwargs"]["cleanup"] is not None


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
        raw = (
            b'{"streams":[{"tags":{"transfer_characteristic":"Apple Log"}}]}'
            if fixture.input.role == "apple_log"
            else b'{"streams":[{"color_transfer":"bt709","tags":{}}]}'
        )
        return BoundedProcessResult(stdout=raw, stderr=b"", returncode=0)

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


def test_missing_production_input_does_not_publish_artifacts(tmp_path):
    detector_root = tmp_path / "missing-detector"
    fixture_root = tmp_path / "fixtures"
    _write_fixture_root(fixture_root)

    with pytest.raises(DetectorValidationError):
        certify_detector(
            rule_input_path=detector_root / "detector-rule-input-v1.json",
            fixture_root=fixture_root,
        )

    assert not (detector_root / "manifest.json").exists()
    assert not (detector_root / "certificate-summary.json").exists()


@pytest.mark.parametrize("target", ["descriptor", "directory", "media"])
def test_resolve_certification_fixtures_rejects_symlinks(tmp_path, target):
    fixture_root = tmp_path / "fixtures"
    _write_fixture_root(fixture_root)
    if target == "descriptor":
        real = tmp_path / "real-descriptor.json"
        os.replace(fixture_root / "fixture-input-v1.json", real)
        (fixture_root / "fixture-input-v1.json").symlink_to(real)
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
