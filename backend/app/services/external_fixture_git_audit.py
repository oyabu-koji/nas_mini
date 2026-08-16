from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.services.bounded_subprocess import BoundedProcessError, run_bounded_process


AUDIT_TIMEOUT_MS = 60_000
AUDIT_MAX_RECORDS = 200_000
AUDIT_MAX_STDOUT_BYTES = 32 * 1024 * 1024
AUDIT_MAX_STDERR_BYTES = 4_096
AUDIT_MAX_FIXTURE_BYTES = 1_099_511_627_776
GIT_OBJECT_ID_PATTERN = re.compile(rb"^[0-9a-f]{40,64}$")
KNOWN_FIXTURE_SHA256 = {
    "A001_04301259_C047.mov": (
        "749f52937f62b1790ac71b37797cf817c877b87dde6ea44969544a46d87032c1"
    ),
    "IMG_0812.MOV": (
        "1c70479d633927d82360322c7f77ba465aee2d31cd2b56dc55e784d09e52237c"
    ),
}


class ExternalFixtureGitAuditError(RuntimeError):
    def __init__(self, code: str = "external_fixture_git_audit_failed"):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExternalFixture:
    path: Path
    expected_sha256: str


@dataclass(frozen=True)
class ExternalFixtureGitAuditResult:
    fixture_count: int
    reachable_record_count: int


def audit_external_fixture_git_history(
    *, repository_root: Path, fixtures: tuple[ExternalFixture, ...]
) -> ExternalFixtureGitAuditResult:
    if not repository_root.is_absolute() or not fixtures:
        raise ExternalFixtureGitAuditError()
    try:
        repository_metadata = repository_root.lstat()
    except OSError as exc:
        raise ExternalFixtureGitAuditError() from exc
    if not stat.S_ISDIR(repository_metadata.st_mode) or repository_root.is_symlink():
        raise ExternalFixtureGitAuditError()

    verified = tuple(_verify_fixture(fixture) for fixture in fixtures)
    object_ids = tuple(
        _derive_fixture_object_id(repository_root, fixture.path)
        for fixture in verified
    )
    try:
        history = run_bounded_process(
            ["git", "rev-list", "--objects", "--all"],
            timeout_ms=AUDIT_TIMEOUT_MS,
            max_stdout_bytes=AUDIT_MAX_STDOUT_BYTES,
            max_stderr_bytes=AUDIT_MAX_STDERR_BYTES,
            cwd=repository_root,
        )
        historical_paths = run_bounded_process(
            ["git", "log", "--all", "--format=", "--name-only", "--no-renames"],
            timeout_ms=AUDIT_TIMEOUT_MS,
            max_stdout_bytes=AUDIT_MAX_STDOUT_BYTES,
            max_stderr_bytes=AUDIT_MAX_STDERR_BYTES,
            cwd=repository_root,
        )
    except (BoundedProcessError, OSError) as exc:
        raise ExternalFixtureGitAuditError() from exc

    fixture_basenames = {fixture.path.name.encode("utf-8") for fixture in verified}
    records = history.stdout.splitlines()
    if len(records) > AUDIT_MAX_RECORDS:
        raise ExternalFixtureGitAuditError()
    for record in records:
        if not record:
            raise ExternalFixtureGitAuditError()
        _object_id, separator, path = record.partition(b" ")
        if separator and _path_is_forbidden(path, fixture_basenames):
            raise ExternalFixtureGitAuditError(
                "external_fixture_git_history_not_clean"
            )
    path_records = [path for path in historical_paths.stdout.splitlines() if path]
    if len(path_records) > AUDIT_MAX_RECORDS:
        raise ExternalFixtureGitAuditError()
    for path in path_records:
        if _path_is_forbidden(path, fixture_basenames):
            raise ExternalFixtureGitAuditError(
                "external_fixture_git_history_not_clean"
            )

    for object_id in object_ids:
        if _fixture_object_exists(repository_root, object_id):
            raise ExternalFixtureGitAuditError(
                "external_fixture_git_object_not_clean"
            )
    return ExternalFixtureGitAuditResult(
        fixture_count=len(verified),
        reachable_record_count=len(records),
    )


def _verify_fixture(fixture: ExternalFixture) -> ExternalFixture:
    known_sha256 = KNOWN_FIXTURE_SHA256.get(fixture.path.name)
    if not fixture.path.is_absolute() or re.fullmatch(
        r"[0-9a-f]{64}", fixture.expected_sha256
    ) is None or fixture.expected_sha256 != known_sha256:
        raise ExternalFixtureGitAuditError()
    descriptor: int | None = None
    try:
        path_before = fixture.path.lstat()
        descriptor = os.open(
            fixture.path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(path_before.st_mode)
            or fixture.path.is_symlink()
            or before.st_size > AUDIT_MAX_FIXTURE_BYTES
            or not _same_identity(path_before, before)
        ):
            raise ExternalFixtureGitAuditError()
        digest = hashlib.sha256()
        size_bytes = 0
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size_bytes += len(chunk)
            after = os.fstat(source.fileno())
        path_after = fixture.path.lstat()
    except OSError as exc:
        raise ExternalFixtureGitAuditError() from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (
        size_bytes != before.st_size
        or not _same_identity(before, after)
        or not _same_identity(after, path_after)
        or digest.hexdigest() != fixture.expected_sha256
    ):
        raise ExternalFixtureGitAuditError()
    return fixture


def _derive_fixture_object_id(repository_root: Path, fixture_path: Path) -> bytes:
    result = _run_small_git(
        repository_root,
        ["git", "hash-object", "--no-filters", str(fixture_path)],
    )
    object_id = result.stdout.strip()
    if result.returncode != 0 or GIT_OBJECT_ID_PATTERN.fullmatch(object_id) is None:
        raise ExternalFixtureGitAuditError()
    return object_id


def _fixture_object_exists(repository_root: Path, object_id: bytes) -> bool:
    result = _run_small_git(
        repository_root,
        ["git", "cat-file", "-e", f"{object_id.decode('ascii')}^{{blob}}"],
    )
    return result.returncode == 0


def _run_small_git(
    repository_root: Path, argv: list[str]
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            argv,
            cwd=repository_root,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=AUDIT_TIMEOUT_MS / 1000,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExternalFixtureGitAuditError() from exc
    if (
        len(result.stdout) > AUDIT_MAX_STDERR_BYTES
        or len(result.stderr) > AUDIT_MAX_STDERR_BYTES
    ):
        raise ExternalFixtureGitAuditError()
    return result


def _path_is_forbidden(path: bytes, fixture_basenames: set[bytes]) -> bool:
    components = path.split(b"/")
    return b"data" in components or bool(fixture_basenames.intersection(components))


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )
