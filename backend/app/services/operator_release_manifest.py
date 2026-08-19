from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

MANIFEST_SCHEMA_VERSION = 2
MAX_ARTIFACT_BYTES = 65_536
OPERATOR_DATABASE_VOLUME = "latest_template_backend-db"
REQUIRED_IMAGE_SERVICES = (
    "api",
    "worker",
    "operator-disposable-marker",
    "operator-migration-identity",
    "startup-migrator",
    "operator-phase2b-migrator",
    "operator-worker-drain",
    "phase2c-migrator-dry-run",
    "phase2c-migrator-apply",
    "detector-v2-preflight",
)
ALLOWED_ENV_KEYS = frozenset(
    {
        "API_TOKEN",
        "DATABASE_PATH",
        "OPERATOR_DISPOSABLE_DATABASE_VOLUME",
        "OPERATOR_DISPOSABLE_NONCE",
        "MEDIA_ROOT",
        "USER_LUT_ROOT",
        "BUILT_IN_PRESET_ROOT",
        "APPLE_LOG_DETECTOR_ROOT",
        "SQLITE_BUSY_TIMEOUT_MS",
        "JOB_LEASE_SECONDS",
        "MEDIA_ROOT_HOST_PATH",
        "USER_LUT_ROOT_HOST_PATH",
    }
)
REQUIRED_ENV_KEYS = frozenset(
    {
        "API_TOKEN",
        "DATABASE_PATH",
        "MEDIA_ROOT",
        "OPERATOR_DISPOSABLE_DATABASE_VOLUME",
        "OPERATOR_DISPOSABLE_NONCE",
    }
)
IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")

CommandRunner = Callable[[list[str], int], str]


class OperatorReleaseManifestError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EnvSourceIdentity:
    filename: str
    sha256: str
    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class OperatorReleaseManifest:
    commit: str
    compose_project: str
    database_volume: str
    disposable_nonce: str
    database_path: str
    release_image_ids: dict[str, str]
    rollback_image_ids: dict[str, str]
    release_env: EnvSourceIdentity
    rollback_env: EnvSourceIdentity


def write_env_source(path: Path, values: Mapping[str, str]) -> EnvSourceIdentity:
    normalized = _validate_env_values(values)
    if path.exists() or path.is_symlink() or path.parent.is_symlink():
        raise OperatorReleaseManifestError("operator_migration_environment_invalid")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = _canonical_json(normalized)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return _load_env_source_identity(path)[0]


def load_env_source(path: Path) -> tuple[EnvSourceIdentity, dict[str, str]]:
    return _load_env_source_identity(path)


def write_manifest(
    path: Path,
    *,
    commit: str,
    compose_project: str,
    database_volume: str,
    disposable_nonce: str,
    database_path: str,
    release_image_ids: Mapping[str, str],
    rollback_image_ids: Mapping[str, str],
    release_env_source: Path,
    rollback_env_source: Path,
) -> OperatorReleaseManifest:
    if path.exists() or path.is_symlink() or path.parent.is_symlink():
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    release_identity, _release_values = load_env_source(release_env_source)
    rollback_identity, _rollback_values = load_env_source(rollback_env_source)
    if release_env_source.parent.resolve() != path.parent.resolve():
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    if rollback_env_source.parent.resolve() != path.parent.resolve():
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    manifest = _validated_manifest(
        commit=commit,
        compose_project=compose_project,
        database_volume=database_volume,
        disposable_nonce=disposable_nonce,
        database_path=database_path,
        release_image_ids=release_image_ids,
        rollback_image_ids=rollback_image_ids,
        release_env=release_identity,
        rollback_env=rollback_identity,
    )
    payload = _canonical_json(_manifest_payload(manifest))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return manifest


def load_manifest(path: Path) -> OperatorReleaseManifest:
    payload, _file_stat = _read_owner_only(path, "operator_migration_manifest_invalid")
    value = _strict_json(payload, "operator_migration_manifest_invalid")
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "commit",
        "compose_project",
        "database_volume",
        "disposable_nonce",
        "database_path",
        "image_ids",
        "env_sources",
    }:
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    if value["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    env_sources = value["env_sources"]
    if not isinstance(env_sources, dict) or set(env_sources) != {"release", "rollback"}:
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    image_ids = value["image_ids"]
    if not isinstance(image_ids, dict) or set(image_ids) != {"release", "rollback"}:
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    release_identity = _parse_env_identity(env_sources["release"])
    rollback_identity = _parse_env_identity(env_sources["rollback"])
    manifest = _validated_manifest(
        commit=value["commit"],
        compose_project=value["compose_project"],
        database_volume=value["database_volume"],
        disposable_nonce=value["disposable_nonce"],
        database_path=value["database_path"],
        release_image_ids=image_ids["release"],
        rollback_image_ids=image_ids["rollback"],
        release_env=release_identity,
        rollback_env=rollback_identity,
    )
    for identity in (manifest.release_env, manifest.rollback_env):
        current, _values = load_env_source(path.parent / identity.filename)
        if current != identity:
            raise OperatorReleaseManifestError(
                "operator_migration_environment_mismatch"
            )
    return manifest


def capture_compose_image_ids(
    *,
    repository_root: Path,
    compose_project: str,
    services: Sequence[str] = REQUIRED_IMAGE_SERVICES,
    command_runner: CommandRunner | None = None,
) -> dict[str, str]:
    run = command_runner or _run_command
    result = {}
    compose = [
        "docker",
        "compose",
        "--project-directory",
        str(repository_root),
        "--file",
        str(repository_root / "docker-compose.yml"),
        "--project-name",
        compose_project,
    ]
    for service in services:
        image_id = run([*compose, "images", "-q", service], 30).strip()
        if not IMAGE_ID_PATTERN.fullmatch(image_id):
            raise OperatorReleaseManifestError("operator_migration_artifact_mismatch")
        result[service] = image_id
    return result


def verify_image_ids(
    expected: Mapping[str, str],
    actual: Mapping[str, str],
) -> None:
    if dict(expected) != dict(actual):
        raise OperatorReleaseManifestError("operator_migration_artifact_mismatch")


def load_image_id_source(path: Path) -> dict[str, str]:
    payload, _file_stat = _read_owner_only(
        path, "operator_migration_artifact_source_invalid"
    )
    value = _strict_json(payload, "operator_migration_artifact_source_invalid")
    return _validate_image_ids(value, "operator_migration_artifact_source_invalid")


def verify_environment(
    identity: EnvSourceIdentity,
    values: Mapping[str, str],
) -> None:
    normalized = _validate_env_values(values)
    if hashlib.sha256(_canonical_json(normalized)).hexdigest() != identity.sha256:
        raise OperatorReleaseManifestError("operator_migration_environment_mismatch")


def _validated_manifest(
    *,
    commit,
    compose_project,
    database_volume,
    disposable_nonce,
    database_path,
    release_image_ids,
    rollback_image_ids,
    release_env,
    rollback_env,
) -> OperatorReleaseManifest:
    if not isinstance(commit, str) or not COMMIT_PATTERN.fullmatch(commit):
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    if not isinstance(compose_project, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9_-]{0,62}", compose_project
    ):
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    if not isinstance(database_volume, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", database_volume
    ):
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    if database_volume == OPERATOR_DATABASE_VOLUME or not database_volume.startswith(
        "disposable-"
    ):
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    if not isinstance(disposable_nonce, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{15,63}", disposable_nonce
    ):
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    if database_path != "/data/mediavault.sqlite3":
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    release_images = _validate_image_ids(
        release_image_ids, "operator_migration_manifest_invalid"
    )
    rollback_images = _validate_image_ids(
        rollback_image_ids, "operator_migration_manifest_invalid"
    )
    return OperatorReleaseManifest(
        commit=commit,
        compose_project=compose_project,
        database_volume=database_volume,
        disposable_nonce=disposable_nonce,
        database_path=database_path,
        release_image_ids=release_images,
        rollback_image_ids=rollback_images,
        release_env=release_env,
        rollback_env=rollback_env,
    )


def _load_env_source_identity(path: Path) -> tuple[EnvSourceIdentity, dict[str, str]]:
    payload, file_stat = _read_owner_only(
        path, "operator_migration_environment_invalid"
    )
    value = _strict_json(payload, "operator_migration_environment_invalid")
    if not isinstance(value, dict):
        raise OperatorReleaseManifestError("operator_migration_environment_invalid")
    normalized = _validate_env_values(value)
    identity = EnvSourceIdentity(
        filename=path.name,
        sha256=hashlib.sha256(_canonical_json(normalized)).hexdigest(),
        device=file_stat.st_dev,
        inode=file_stat.st_ino,
        size=file_stat.st_size,
        mtime_ns=file_stat.st_mtime_ns,
    )
    return identity, normalized


def _validate_env_values(values: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(values, Mapping):
        raise OperatorReleaseManifestError("operator_migration_environment_invalid")
    if not REQUIRED_ENV_KEYS.issubset(values) or not set(values).issubset(
        ALLOWED_ENV_KEYS
    ):
        raise OperatorReleaseManifestError("operator_migration_environment_invalid")
    if any(
        not isinstance(key, str) or not isinstance(value, str) or not value
        for key, value in values.items()
    ):
        raise OperatorReleaseManifestError("operator_migration_environment_invalid")
    return {key: values[key] for key in sorted(values)}


def _validate_image_ids(value, code: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(REQUIRED_IMAGE_SERVICES):
        raise OperatorReleaseManifestError(code)
    if any(
        not isinstance(image_id, str) or not IMAGE_ID_PATTERN.fullmatch(image_id)
        for image_id in value.values()
    ):
        raise OperatorReleaseManifestError(code)
    return {service: value[service] for service in REQUIRED_IMAGE_SERVICES}


def _read_owner_only(path: Path, code: str) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise OperatorReleaseManifestError(code)
        if before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o600:
            raise OperatorReleaseManifestError(code)
        if before.st_size > MAX_ARTIFACT_BYTES:
            raise OperatorReleaseManifestError(code)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            payload = os.read(descriptor, MAX_ARTIFACT_BYTES + 1)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except (OSError, ValueError) as exc:
        raise OperatorReleaseManifestError(code) from exc
    if len(payload) > MAX_ARTIFACT_BYTES or (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise OperatorReleaseManifestError(code)
    return payload, after


def _strict_json(payload: bytes, code: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise OperatorReleaseManifestError(code)
            result[key] = value
        return result

    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperatorReleaseManifestError(code) from exc


def _parse_env_identity(value) -> EnvSourceIdentity:
    if not isinstance(value, dict) or set(value) != {
        "filename",
        "sha256",
        "device",
        "inode",
        "size",
        "mtime_ns",
    }:
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    filename = value["filename"]
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    sha256_value = value["sha256"]
    if not isinstance(sha256_value, str) or not re.fullmatch(
        r"[0-9a-f]{64}", sha256_value
    ):
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    numeric = (value["device"], value["inode"], value["size"], value["mtime_ns"])
    if any(not isinstance(item, int) or item < 0 for item in numeric):
        raise OperatorReleaseManifestError("operator_migration_manifest_invalid")
    return EnvSourceIdentity(
        filename=filename,
        sha256=sha256_value,
        device=value["device"],
        inode=value["inode"],
        size=value["size"],
        mtime_ns=value["mtime_ns"],
    )


def _manifest_payload(manifest: OperatorReleaseManifest) -> dict:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "commit": manifest.commit,
        "compose_project": manifest.compose_project,
        "database_volume": manifest.database_volume,
        "disposable_nonce": manifest.disposable_nonce,
        "database_path": manifest.database_path,
        "image_ids": {
            "release": manifest.release_image_ids,
            "rollback": manifest.rollback_image_ids,
        },
        "env_sources": {
            "release": vars(manifest.release_env),
            "rollback": vars(manifest.rollback_env),
        },
    }


def _canonical_json(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _run_command(argv: list[str], timeout_seconds: int) -> str:
    try:
        completed = subprocess.run(
            argv,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OperatorReleaseManifestError(
            "operator_migration_artifact_mismatch"
        ) from exc
    if completed.returncode != 0 or len(completed.stdout.encode("utf-8")) > 1_048_576:
        raise OperatorReleaseManifestError("operator_migration_artifact_mismatch")
    return completed.stdout
