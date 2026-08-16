from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.services.detector_manifest import (
    DETECTOR_ID,
    DETECTOR_MAX_EVIDENCE_BYTES,
    DETECTOR_MAX_STDERR_BYTES,
    DETECTOR_MAX_STDOUT_BYTES,
    DETECTOR_PROBE_TIMEOUT_MS,
    FFPROBE_SHOW_ENTRIES,
    MANIFEST_SCHEMA_VERSION,
    DetectorValidationError,
    DetectorManifest,
    FFPROBE_PROBE_ARGUMENTS,
    RuleInput,
    canonical_document,
    document_with_digest,
    load_rule_input,
)
from app.services.detector_fixture_descriptor import (
    LocalFixtureInput,
    confine_fixture_path,
    load_local_fixture_descriptor,
    validate_fixture_root,
)
from app.services.bounded_subprocess import BoundedProcessResult, run_bounded_process
from app.services.apple_log_detector import classify_detection
from app.services.detector_inspection import INSPECTION_MAX_BYTES, parse_inspection
from app.services.synthetic_detector_fixture import (
    build_apple_log_1_synthetic_container,
)
from app.services.detector_snapshot_cleanup import (
    SNAPSHOT_NAMESPACE_PATTERN,
    remove_snapshot_namespace,
    sweep_stale_snapshot_namespaces,
)


FIXTURE_DESCRIPTOR_NAME = "detector-certification-v2.json"
CONTAINER_NAME_PATTERN = re.compile(r"^mediavault-detector-certifier-[0-9a-f]{32}$")
CERTIFIER_PROFILE = "detector-certification"
CERTIFIER_SERVICE = "detector-certifier"


@dataclass(frozen=True)
class ResolvedFixture:
    input: LocalFixtureInput
    media_path: Path


@dataclass(frozen=True)
class VerifiedFixture:
    input: LocalFixtureInput | "SyntheticFixtureInput"
    media_path: Path
    media_sha256: str
    size_bytes: int


@dataclass(frozen=True)
class CertificationResult:
    manifest_sha256: str
    rule_input_sha256: str


@dataclass(frozen=True)
class SyntheticFixtureInput:
    evidence_class: str = "synthetic-container"
    expected_detection_status: str = "apple_log"
    expected_sha256: str = ""
    expected_source_profile: str | None = "apple-log-1"
    path: str = "synthetic-apple-log-1.mov"
    provenance: str = "project-owned-synthetic-container"
    role: str = "apple-log-1"


def resolve_certification_fixtures(fixture_root: Path) -> tuple[ResolvedFixture, ...]:
    if not fixture_root.is_absolute():
        raise DetectorValidationError()
    validate_fixture_root(fixture_root)
    descriptor_path = fixture_root / FIXTURE_DESCRIPTOR_NAME
    descriptor = load_local_fixture_descriptor(descriptor_path)
    return tuple(
        ResolvedFixture(
            input=fixture,
            media_path=_resolve_regular_file(
                fixture_root,
                confine_fixture_path(fixture_root, fixture.path)
                .relative_to(fixture_root)
                .as_posix(),
            ),
        )
        for fixture in descriptor.fixtures
    )


def verify_fixture_media(fixture: ResolvedFixture) -> VerifiedFixture:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            fixture.media_path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise DetectorValidationError()
        digest = hashlib.sha256()
        size_bytes = 0
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size_bytes += len(chunk)
            after = os.fstat(source.fileno())
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or size_bytes != before.st_size
        ):
            raise DetectorValidationError()
    except OSError as exc:
        raise DetectorValidationError() from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)

    actual = digest.hexdigest()
    if actual != fixture.input.expected_sha256:
        raise DetectorValidationError()
    return VerifiedFixture(
        input=fixture.input,
        media_path=fixture.media_path,
        media_sha256=actual,
        size_bytes=size_bytes,
    )


def run_certifier_probe(
    *, fixture_root: Path, fixture: VerifiedFixture
) -> BoundedProcessResult:
    container_name = _new_container_name()
    container_path = "/fixtures/" + fixture.input.path
    return _run_certifier(
        fixture_root=fixture_root,
        container_name=container_name,
        command_arguments=[
            "python",
            "-m",
            "scripts.inspect_detector_fixture",
            "--fixture",
            container_path,
        ],
        max_stdout_bytes=INSPECTION_MAX_BYTES,
    )


def run_certifier_version(*, fixture_root: Path) -> BoundedProcessResult:
    return _run_certifier(
        fixture_root=fixture_root,
        container_name=_new_container_name(),
        command_arguments=["ffprobe", "-version"],
    )


def certify_detector(*, rule_input_path: Path, fixture_root: Path) -> CertificationResult:
    rule_input = load_rule_input(rule_input_path)
    resolved_fixtures = resolve_certification_fixtures(fixture_root)
    sweep_stale_snapshot_namespaces()
    verified_sources = tuple(
        verify_fixture_media(fixture) for fixture in resolved_fixtures
    )
    with _temporary_snapshot_namespace() as snapshot_root:
        external_fixtures = tuple(
            _snapshot_fixture_media(fixture, snapshot_root=snapshot_root)
            for fixture in verified_sources
        )
        fixtures = (
            _create_synthetic_apple_log_1_fixture(snapshot_root),
            *external_fixtures,
        )
        return _certify_snapshot(
            rule_input=rule_input,
            artifact_directory=rule_input_path.parent,
            fixture_root=snapshot_root,
            fixtures=fixtures,
        )


def _certify_snapshot(
    *,
    rule_input: RuleInput,
    artifact_directory: Path,
    fixture_root: Path,
    fixtures: tuple[VerifiedFixture, ...],
) -> CertificationResult:
    version = _read_version(run_certifier_version(fixture_root=fixture_root).stdout)
    ephemeral_manifest = _ephemeral_manifest(rule_input, version)
    for fixture in fixtures:
        inspection_process = run_certifier_probe(
            fixture_root=fixture_root, fixture=fixture
        )
        inspection = parse_inspection(inspection_process.stdout)
        result = classify_detection(
            container=inspection.container,
            probe=inspection.probe,
            manifest=ephemeral_manifest,
        )
        if (
            result.status != fixture.input.expected_detection_status
            or result.source_profile != fixture.input.expected_source_profile
        ):
            raise DetectorValidationError()

    approved = json.loads(rule_input.canonical_bytes)
    fixture_identity = [
        {
            "role": fixture.input.role,
            "evidence_class": fixture.input.evidence_class,
            "sha256": fixture.media_sha256,
            "expected_detection_status": fixture.input.expected_detection_status,
            "expected_source_profile": fixture.input.expected_source_profile,
            "provenance": fixture.input.provenance,
        }
        for fixture in fixtures
    ]
    manifest_value = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "detector_id": DETECTOR_ID,
        "rule_version": rule_input.rule_version,
        "rule_input_sha256": rule_input.sha256,
        "parser_contract_version": approved["parser_contract_version"],
        "identifier_mappings": approved["identifier_mappings"],
        "profile_preset_mappings": approved["profile_preset_mappings"],
        "color_allowlists": approved["color_allowlists"],
        "not_log_predicate": approved["not_log_predicate"],
        "resource_limits": approved["resource_limits"],
        "official_source_url": approved["official_source_url"],
        "ffprobe_version": version,
        "show_entries": FFPROBE_SHOW_ENTRIES,
        "timeout_ms": DETECTOR_PROBE_TIMEOUT_MS,
        "max_stdout_bytes": DETECTOR_MAX_STDOUT_BYTES,
        "max_stderr_bytes": DETECTOR_MAX_STDERR_BYTES,
        "max_evidence_bytes": DETECTOR_MAX_EVIDENCE_BYTES,
        "fixtures": fixture_identity,
    }
    manifest_bytes = document_with_digest(manifest_value, "manifest_sha256")
    manifest_sha256 = json.loads(manifest_bytes)["manifest_sha256"]
    summary_bytes = canonical_document(
        {
            "schema_version": 2,
            "detector_id": DETECTOR_ID,
            "manifest_sha256": manifest_sha256,
            "rule_input_sha256": rule_input.sha256,
            "parser_contract_version": rule_input.parser_contract_version,
            "ffprobe_version": version,
            "future_apple_log_1_transform_allowed": False,
            "fixtures": [
                {
                    "role": fixture.input.role,
                    "evidence_class": fixture.input.evidence_class,
                    "sha256": fixture.media_sha256,
                    "expected_detection_status": fixture.input.expected_detection_status,
                    "expected_source_profile": fixture.input.expected_source_profile,
                }
                for fixture in fixtures
            ],
        }
    )
    _publish_artifacts(
        target_directory=artifact_directory,
        artifacts={
            "manifest.json": manifest_bytes,
            "certificate-summary.json": summary_bytes,
        },
    )
    return CertificationResult(
        manifest_sha256=manifest_sha256,
        rule_input_sha256=rule_input.sha256,
    )


@contextmanager
def _temporary_snapshot_namespace():
    parent = Path(tempfile.gettempdir())
    snapshot_root = parent / f"mediavault-detector-fixtures-{uuid.uuid4().hex}"
    if SNAPSHOT_NAMESPACE_PATTERN.fullmatch(snapshot_root.name) is None:
        raise DetectorValidationError()
    try:
        snapshot_root.mkdir(mode=0o700)
        os.chmod(snapshot_root, 0o700)
    except OSError as exc:
        raise DetectorValidationError() from exc
    with _catchable_signal_cleanup(snapshot_root):
        try:
            yield snapshot_root
        finally:
            remove_snapshot_namespace(snapshot_root)


@contextmanager
def _catchable_signal_cleanup(snapshot_root: Path):
    previous: dict[int, object] = {}

    def handle(signum, _frame):
        remove_snapshot_namespace(snapshot_root)
        if signum == signal.SIGINT:
            raise KeyboardInterrupt()
        raise SystemExit(128 + signum)

    try:
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, handle)
    except ValueError:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        previous.clear()
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _snapshot_fixture_media(
    fixture: ResolvedFixture | VerifiedFixture, *, snapshot_root: Path
) -> VerifiedFixture:
    destination = snapshot_root.joinpath(
        *PurePosixPath(fixture.input.path).parts
    )
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    source_descriptor: int | None = None
    destination_descriptor: int | None = None
    try:
        source_descriptor = os.open(
            fixture.media_path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise DetectorValidationError()
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        digest = hashlib.sha256()
        remaining = before.st_size
        with (
            os.fdopen(source_descriptor, "rb") as source,
            os.fdopen(destination_descriptor, "wb") as output,
        ):
            source_descriptor = None
            destination_descriptor = None
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise DetectorValidationError()
                output.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            if source.read(1):
                raise DetectorValidationError()
            after = os.fstat(source.fileno())
            output.flush()
            os.fsync(output.fileno())
            copied = os.fstat(output.fileno())
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or copied.st_size != before.st_size
        ):
            raise DetectorValidationError()
    except OSError as exc:
        raise DetectorValidationError() from exc
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)

    actual = digest.hexdigest()
    if actual != fixture.input.expected_sha256:
        raise DetectorValidationError()
    try:
        os.chmod(destination, 0o400)
    except OSError as exc:
        raise DetectorValidationError() from exc
    return VerifiedFixture(
        input=fixture.input,
        media_path=destination,
        media_sha256=actual,
        size_bytes=before.st_size,
    )


def _create_synthetic_apple_log_1_fixture(snapshot_root: Path) -> VerifiedFixture:
    content = build_apple_log_1_synthetic_container(track_id=1)
    destination = snapshot_root / "synthetic-apple-log-1.mov"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(destination, 0o400)
    except OSError as exc:
        raise DetectorValidationError() from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    digest = hashlib.sha256(content).hexdigest()
    return VerifiedFixture(
        input=SyntheticFixtureInput(expected_sha256=digest),
        media_path=destination,
        media_sha256=digest,
        size_bytes=len(content),
    )


def _ephemeral_manifest(rule_input: RuleInput, version: str) -> DetectorManifest:
    return DetectorManifest(
        detector_id=DETECTOR_ID,
        rule_version=rule_input.rule_version,
        rule_input_sha256=rule_input.sha256,
        parser_contract_version=rule_input.parser_contract_version,
        identifier_mappings=rule_input.identifier_mappings,
        profile_preset_mappings=rule_input.profile_preset_mappings,
        color_allowlists=rule_input.color_allowlists,
        not_log_predicate=rule_input.not_log_predicate,
        resource_limits=rule_input.resource_limits,
        official_source_url=rule_input.official_source_url,
        ffprobe_version=version,
        show_entries=FFPROBE_SHOW_ENTRIES,
        timeout_ms=DETECTOR_PROBE_TIMEOUT_MS,
        max_stdout_bytes=DETECTOR_MAX_STDOUT_BYTES,
        max_stderr_bytes=DETECTOR_MAX_STDERR_BYTES,
        max_evidence_bytes=DETECTOR_MAX_EVIDENCE_BYTES,
        fixtures=(),
        manifest_sha256="0" * 64,
        canonical_bytes=b"",
    )


def _read_version(raw: bytes) -> str:
    try:
        version = raw.decode("utf-8", errors="strict").splitlines()[0].strip()
    except (UnicodeError, IndexError) as exc:
        raise DetectorValidationError("log_detector_version_mismatch") from exc
    if not version or len(version) > 256:
        raise DetectorValidationError("log_detector_version_mismatch")
    return version


def _publish_artifacts(*, target_directory: Path, artifacts: dict[str, bytes]) -> None:
    candidates: list[tuple[Path, Path]] = []
    try:
        for filename, content in artifacts.items():
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{filename}.",
                suffix=".tmp",
                dir=target_directory,
            )
            temporary_path = Path(temporary_name)
            candidates.append((temporary_path, target_directory / filename))
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary_path, 0o600)
        for temporary_path, target_path in candidates:
            os.replace(temporary_path, target_path)
        directory_descriptor = os.open(target_directory, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as exc:
        raise DetectorValidationError() from exc
    finally:
        for temporary_path, _target_path in candidates:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _run_certifier(
    *,
    fixture_root: Path,
    container_name: str,
    command_arguments: list[str],
    max_stdout_bytes: int = DETECTOR_MAX_STDOUT_BYTES,
) -> BoundedProcessResult:
    if CONTAINER_NAME_PATTERN.fullmatch(container_name) is None:
        raise DetectorValidationError()
    repository_root = Path(__file__).resolve().parents[3]
    argv = [
        "docker",
        "compose",
        "--project-directory",
        str(repository_root),
        "--profile",
        CERTIFIER_PROFILE,
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--name",
        container_name,
        CERTIFIER_SERVICE,
        *command_arguments,
    ]
    return run_bounded_process(
        argv,
        timeout_ms=DETECTOR_PROBE_TIMEOUT_MS,
        max_stdout_bytes=max_stdout_bytes,
        max_stderr_bytes=DETECTOR_MAX_STDERR_BYTES,
        cwd=repository_root,
        cleanup=lambda: _remove_certifier_container(container_name),
        env={**os.environ, "DETECTOR_FIXTURE_ROOT": str(fixture_root)},
    )


def _new_container_name() -> str:
    name = f"mediavault-detector-certifier-{uuid.uuid4().hex}"
    if CONTAINER_NAME_PATTERN.fullmatch(name) is None:
        raise DetectorValidationError()
    return name


def _remove_certifier_container(container_name: str) -> bool:
    if CONTAINER_NAME_PATTERN.fullmatch(container_name) is None:
        return False
    try:
        removed = subprocess.run(
            ["docker", "rm", "-f", container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        if removed.returncode == 0:
            return True
        inspected = subprocess.run(
            ["docker", "inspect", container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return inspected.returncode != 0


def _resolve_regular_file(root: Path, relative_path: str) -> Path:
    current = root
    for index, component in enumerate(PurePosixPath(relative_path).parts):
        current = current / component
        if index == len(PurePosixPath(relative_path).parts) - 1:
            _require_regular_no_symlink(current)
        else:
            _require_directory_no_symlink(current)
    try:
        current.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise DetectorValidationError() from exc
    return current


def _require_regular_no_symlink(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DetectorValidationError() from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise DetectorValidationError()


def _require_directory_no_symlink(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DetectorValidationError() from exc
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise DetectorValidationError()
