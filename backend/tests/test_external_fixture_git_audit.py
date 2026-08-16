import hashlib
import json
import os
import subprocess

import pytest

from app.services.bounded_subprocess import BoundedProcessError, BoundedProcessResult
from app.services.external_fixture_git_audit import (
    AUDIT_MAX_RECORDS,
    AUDIT_MAX_STDOUT_BYTES,
    AUDIT_TIMEOUT_MS,
    KNOWN_FIXTURE_SHA256,
    ExternalFixture,
    ExternalFixtureGitAuditError,
    audit_external_fixture_git_history,
)
from app.services import external_fixture_git_audit
from scripts import audit_external_fixture_git_history as audit_cli


def _git(repository, *arguments):
    subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_external_fixture_git_audit_accepts_clean_repository(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("clean history\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "--quiet", "-m", "initial")
    fixture_root = repository / "data"
    fixture_root.mkdir()
    fixture = fixture_root / "recording.mov"
    fixture.write_bytes(b"local-only-fixture")
    expected_sha256 = hashlib.sha256(fixture.read_bytes()).hexdigest()
    monkeypatch.setattr(
        external_fixture_git_audit,
        "KNOWN_FIXTURE_SHA256",
        {fixture.name: expected_sha256},
    )
    original_runner = external_fixture_git_audit.run_bounded_process
    captured_history_calls = []

    def capture_history_call(argv, **kwargs):
        captured_history_calls.append({"argv": argv, "kwargs": kwargs})
        return original_runner(argv, **kwargs)

    monkeypatch.setattr(
        external_fixture_git_audit, "run_bounded_process", capture_history_call
    )

    result = audit_external_fixture_git_history(
        repository_root=repository,
        fixtures=(
            ExternalFixture(
                path=fixture,
                expected_sha256=expected_sha256,
            ),
        ),
    )

    assert result.fixture_count == 1
    assert result.reachable_record_count == 3
    rev_list_call = next(
        call for call in captured_history_calls if call["argv"][1] == "rev-list"
    )
    assert rev_list_call["argv"] == [
        "git",
        "rev-list",
        "--objects",
        "--all",
    ]
    assert rev_list_call["kwargs"]["timeout_ms"] == 60_000
    assert rev_list_call["kwargs"]["max_stdout_bytes"] == 32 * 1024 * 1024
    fixture_oid = subprocess.run(
        ["git", "hash-object", "--no-filters", str(fixture)],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()
    object_lookup = subprocess.run(
        ["git", "cat-file", "-e", f"{fixture_oid.decode('ascii')}^{{blob}}"],
        cwd=repository,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert object_lookup.returncode != 0


def test_external_fixture_git_audit_cli_uses_fixed_local_descriptor(
    tmp_path, monkeypatch, capsys
):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("clean history\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "--quiet", "-m", "initial")
    fixture_root = repository / "data"
    fixture_root.mkdir(mode=0o700)
    os.chmod(fixture_root, 0o700)
    apple = fixture_root / "apple.mov"
    ordinary = fixture_root / "ordinary.mov"
    apple.write_bytes(b"apple")
    ordinary.write_bytes(b"ordinary")
    descriptor = fixture_root / "detector-certification-v2.json"
    descriptor.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "fixtures": [
                    {
                        "evidence_class": "real-container",
                        "expected_detection_status": "apple_log",
                        "expected_sha256": hashlib.sha256(b"apple").hexdigest(),
                        "expected_source_profile": "apple-log-2",
                        "path": "apple.mov",
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
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    os.chmod(descriptor, 0o600)
    monkeypatch.setattr(audit_cli, "REPOSITORY_ROOT", repository)
    monkeypatch.setattr(
        external_fixture_git_audit,
        "KNOWN_FIXTURE_SHA256",
        {
            apple.name: hashlib.sha256(b"apple").hexdigest(),
            ordinary.name: hashlib.sha256(b"ordinary").hexdigest(),
        },
    )

    assert audit_cli.main([]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith("external_fixture_git_audit_ok fixture_count=2 ")
    assert str(repository) not in captured.out


def test_external_fixture_git_audit_pins_known_fixture_sha256_values():
    assert KNOWN_FIXTURE_SHA256 == {
        "A001_04301259_C047.mov": (
            "749f52937f62b1790ac71b37797cf817c877b87dde6ea44969544a46d87032c1"
        ),
        "IMG_0812.MOV": (
            "1c70479d633927d82360322c7f77ba465aee2d31cd2b56dc55e784d09e52237c"
        ),
    }


def test_external_fixture_git_audit_limits_are_fixed():
    assert AUDIT_TIMEOUT_MS == 60_000
    assert AUDIT_MAX_RECORDS == 200_000
    assert AUDIT_MAX_STDOUT_BYTES == 32 * 1024 * 1024


@pytest.mark.parametrize(
    "tracked_path",
    ["data/harmless.txt", "archive/recording.mov"],
)
def test_external_fixture_git_audit_rejects_data_component_and_fixture_basename(
    tmp_path, monkeypatch, tracked_path
):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test")
    tracked = repository / tracked_path
    tracked.parent.mkdir(parents=True)
    tracked.write_bytes(b"tracked-but-not-fixture")
    _git(repository, "add", tracked_path)
    _git(repository, "commit", "--quiet", "-m", "forbidden path")
    fixture_root = repository / "local"
    fixture_root.mkdir()
    fixture = fixture_root / "recording.mov"
    fixture.write_bytes(b"local-only-fixture")
    expected_sha256 = hashlib.sha256(fixture.read_bytes()).hexdigest()
    monkeypatch.setattr(
        external_fixture_git_audit,
        "KNOWN_FIXTURE_SHA256",
        {fixture.name: expected_sha256},
    )

    with pytest.raises(ExternalFixtureGitAuditError) as raised:
        audit_external_fixture_git_history(
            repository_root=repository,
            fixtures=(
                ExternalFixture(
                    path=fixture,
                    expected_sha256=expected_sha256,
                ),
            ),
        )

    assert raised.value.code == "external_fixture_git_history_not_clean"


def test_external_fixture_git_audit_rejects_unreachable_exact_fixture_blob(
    tmp_path, monkeypatch
):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("clean history\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "--quiet", "-m", "initial")
    fixture_root = repository / "local"
    fixture_root.mkdir()
    fixture = fixture_root / "recording.mov"
    fixture.write_bytes(b"local-only-fixture")
    expected_sha256 = hashlib.sha256(fixture.read_bytes()).hexdigest()
    monkeypatch.setattr(
        external_fixture_git_audit,
        "KNOWN_FIXTURE_SHA256",
        {fixture.name: expected_sha256},
    )
    _git(repository, "hash-object", "-w", "--no-filters", str(fixture))
    fixture_oid = subprocess.run(
        ["git", "hash-object", "--no-filters", str(fixture)],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.decode("ascii").strip()

    with pytest.raises(ExternalFixtureGitAuditError) as raised:
        audit_external_fixture_git_history(
            repository_root=repository,
            fixtures=(
                ExternalFixture(
                    path=fixture,
                    expected_sha256=expected_sha256,
                ),
            ),
        )

    assert raised.value.code == "external_fixture_git_object_not_clean"
    safe_error = str(raised.value)
    assert str(fixture) not in safe_error
    assert fixture.name not in safe_error
    assert "local-only-fixture" not in safe_error
    assert fixture_oid not in safe_error


@pytest.mark.parametrize("limit", ["timeout", "stdout", "records"])
def test_external_fixture_git_audit_fails_closed_on_each_history_limit(
    tmp_path, monkeypatch, limit
):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    fixture = repository / "recording.mov"
    fixture.write_bytes(b"local-only-fixture")
    expected_sha256 = hashlib.sha256(fixture.read_bytes()).hexdigest()
    monkeypatch.setattr(
        external_fixture_git_audit,
        "KNOWN_FIXTURE_SHA256",
        {fixture.name: expected_sha256},
    )

    def limited_history(*_args, **_kwargs):
        if limit == "timeout":
            raise BoundedProcessError("log_probe_timeout")
        if limit == "stdout":
            raise BoundedProcessError("log_probe_output_invalid")
        return BoundedProcessResult(
            stdout=b"0\n" * (AUDIT_MAX_RECORDS + 1),
            stderr=b"",
            returncode=0,
        )

    monkeypatch.setattr(
        external_fixture_git_audit, "run_bounded_process", limited_history
    )

    with pytest.raises(ExternalFixtureGitAuditError) as raised:
        audit_external_fixture_git_history(
            repository_root=repository,
            fixtures=(
                ExternalFixture(
                    path=fixture,
                    expected_sha256=expected_sha256,
                ),
            ),
        )

    assert raised.value.code == "external_fixture_git_audit_failed"


def test_external_fixture_git_audit_rejects_historical_fixture_basename_after_rename(
    tmp_path, monkeypatch
):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test")
    historical = repository / "recording.mov"
    historical.write_bytes(b"historical-placeholder")
    _git(repository, "add", "recording.mov")
    _git(repository, "commit", "--quiet", "-m", "historical fixture name")
    _git(repository, "mv", "recording.mov", "renamed-placeholder.bin")
    _git(repository, "commit", "--quiet", "-m", "rename placeholder")
    fixture_root = repository / "local"
    fixture_root.mkdir()
    fixture = fixture_root / "recording.mov"
    fixture.write_bytes(b"local-only-fixture")
    expected_sha256 = hashlib.sha256(fixture.read_bytes()).hexdigest()
    monkeypatch.setattr(
        external_fixture_git_audit,
        "KNOWN_FIXTURE_SHA256",
        {fixture.name: expected_sha256},
    )

    with pytest.raises(ExternalFixtureGitAuditError) as raised:
        audit_external_fixture_git_history(
            repository_root=repository,
            fixtures=(
                ExternalFixture(
                    path=fixture,
                    expected_sha256=expected_sha256,
                ),
            ),
        )

    assert raised.value.code == "external_fixture_git_history_not_clean"


def test_external_fixture_git_audit_rejects_fixture_sha256_mismatch(
    tmp_path, monkeypatch
):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    fixture = repository / "recording.mov"
    fixture.write_bytes(b"local-only-fixture")
    declared_sha256 = "a" * 64
    monkeypatch.setattr(
        external_fixture_git_audit,
        "KNOWN_FIXTURE_SHA256",
        {fixture.name: declared_sha256},
    )

    with pytest.raises(ExternalFixtureGitAuditError) as raised:
        audit_external_fixture_git_history(
            repository_root=repository,
            fixtures=(
                ExternalFixture(
                    path=fixture,
                    expected_sha256=declared_sha256,
                ),
            ),
        )

    assert raised.value.code == "external_fixture_git_audit_failed"
