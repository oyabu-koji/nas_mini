from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from app.services.operator_release_manifest import (
    OPERATOR_DATABASE_VOLUME,
    EnvSourceIdentity,
    OperatorReleaseManifest,
    OperatorReleaseManifestError,
    capture_compose_image_ids,
    load_env_source,
    load_manifest,
    verify_image_ids,
)


@dataclass(frozen=True)
class HostCommandResult:
    returncode: int
    stdout: str


@dataclass(frozen=True)
class ContainerSnapshot:
    container_id: str
    service: str
    compose_project: str
    image_id: str
    state: str
    exit_code: int
    environment: dict[str, str]
    mounts: tuple[dict, ...]
    host_config: dict
    command: tuple[str, ...]
    entrypoint: tuple[str, ...]


@dataclass(frozen=True)
class OperatorReleaseResult:
    status: str
    completed_phases: tuple[str, ...]
    services_stopped: bool


CommandRunner = Callable[[list[str], int, Mapping[str, str] | None], HostCommandResult]

SERVICE_PROFILES = {
    "operator-disposable-marker": "operator-migration",
    "operator-migration-identity": "operator-migration",
    "startup-migrator": "operator-migration",
    "operator-phase2b-migrator": "operator-migration",
    "operator-worker-drain": "operator-migration",
    "phase2c-migrator-dry-run": "operator-migration",
    "phase2c-migrator-apply": "operator-migration",
    "detector-v2-preflight": "detector-v2-preflight",
}
KNOWN_STOPPED_SERVICES = frozenset({"api", "worker"})
FIXED_MIGRATOR_ENV = {
    "operator-phase2b-migrator": {
        "API_TOKEN": "migration-not-exposed",
        "DATABASE_PATH": "/data/mediavault.sqlite3",
        "MEDIA_ROOT": "/unmounted-media",
        "USER_LUT_ROOT": "",
    },
    "phase2c-migrator-dry-run": {
        "API_TOKEN": "migration-not-exposed",
        "DATABASE_PATH": "/data/mediavault.sqlite3",
        "MEDIA_ROOT": "/unmounted-media",
        "USER_LUT_ROOT": "",
    },
    "phase2c-migrator-apply": {
        "API_TOKEN": "migration-not-exposed",
        "DATABASE_PATH": "/data/mediavault.sqlite3",
        "MEDIA_ROOT": "/unmounted-media",
        "USER_LUT_ROOT": "",
    },
}
FIXED_SERVICE_COMMANDS = {
    "operator-disposable-marker": (
        "uv",
        "run",
        "--frozen",
        "--no-dev",
        "python",
        "-m",
        "scripts.initialize_disposable_database_target",
    ),
    "operator-migration-identity": (
        "uv",
        "run",
        "--frozen",
        "--no-dev",
        "python",
        "-m",
        "scripts.inspect_operator_migration_identity",
    ),
    "startup-migrator": (
        "uv",
        "run",
        "--frozen",
        "--no-dev",
        "python",
        "-m",
        "scripts.migrate_startup_offline",
        "--apply",
        "--offline-maintenance-confirmed",
    ),
    "operator-phase2b-migrator": (
        "uv",
        "run",
        "--frozen",
        "--no-dev",
        "python",
        "-m",
        "scripts.migrate_phase2b_operator_safe",
    ),
    "operator-worker-drain": (
        "uv",
        "run",
        "--frozen",
        "--no-dev",
        "python",
        "-m",
        "scripts.run_operator_worker_drain",
    ),
    "phase2c-migrator-dry-run": (
        "uv",
        "run",
        "--frozen",
        "--no-dev",
        "python",
        "-m",
        "scripts.migrate_phase2c_operator_safe",
        "--dry-run",
        "--offline-maintenance-confirmed",
    ),
    "phase2c-migrator-apply": (
        "uv",
        "run",
        "--frozen",
        "--no-dev",
        "python",
        "-m",
        "scripts.migrate_phase2c_operator_safe",
        "--apply",
        "--offline-maintenance-confirmed",
    ),
    "detector-v2-preflight": (
        "uv",
        "run",
        "--frozen",
        "--no-dev",
        "python",
        "-m",
        "scripts.preflight_detector_v2",
    ),
}
ROLLBACK_SERVICE_COMMANDS = {
    "api": (
        "uv",
        "run",
        "--frozen",
        "--no-dev",
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ),
    "worker": (
        "uv",
        "run",
        "--frozen",
        "--no-dev",
        "python",
        "-m",
        "app.workers.worker",
    ),
}


class OperatorReleaseOrchestrationError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        restore_required: bool = False,
        services_stopped: bool = False,
        last_committed_version: str | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.restore_required = restore_required
        self.services_stopped = services_stopped
        self.last_committed_version = last_committed_version


def run_operator_release_orchestration(
    *,
    repository_root: Path,
    manifest_path: Path,
    command_runner: CommandRunner | None = None,
    drain_timeout_seconds: int = 300,
) -> OperatorReleaseResult:
    repository_root = repository_root.resolve()
    if not (repository_root / "docker-compose.yml").is_file():
        raise OperatorReleaseOrchestrationError(
            "operator_migration_invalid_working_directory"
        )
    manifest = load_manifest(manifest_path)
    if manifest.database_volume == OPERATOR_DATABASE_VOLUME:
        raise OperatorReleaseOrchestrationError(
            "operator_migration_operator_volume_forbidden"
        )
    run = command_runner or _run_command
    _require_disposable_volume(run, manifest)
    current_commit = _require_success(
        run(["git", "-C", str(repository_root), "rev-parse", "HEAD"], 30, None),
        "operator_migration_commit_mismatch",
    ).stdout.strip()
    if current_commit != manifest.commit:
        raise OperatorReleaseOrchestrationError("operator_migration_commit_mismatch")
    tracked_contract = run(
        [
            "git",
            "-C",
            str(repository_root),
            "diff",
            "--quiet",
            manifest.commit,
            "--",
            "docker-compose.yml",
            "backend/app/services/operator_release_manifest.py",
            "backend/app/services/operator_migration_identity.py",
            "backend/app/services/operator_release_orchestration.py",
            "backend/app/services/disposable_database_target.py",
            "backend/scripts",
        ],
        30,
        None,
    )
    if tracked_contract.returncode != 0:
        raise OperatorReleaseOrchestrationError("operator_migration_commit_mismatch")
    release_identity, release_values = load_env_source(
        manifest_path.parent / manifest.release_env.filename
    )
    _require_identity(release_identity, manifest.release_env)
    _require_manifest_volume_environment(manifest, release_values)
    actual_images = capture_compose_image_ids(
        repository_root=repository_root,
        compose_project=manifest.compose_project,
        command_runner=lambda argv, timeout: (
            _require_success(
                run(argv, timeout, release_values),
                "operator_migration_artifact_mismatch",
            ).stdout
        ),
    )
    verify_image_ids(manifest.release_image_ids, actual_images)
    _claim_operation(manifest_path, manifest)

    compose = _compose_argv(repository_root, manifest)
    created_ids: set[str] = set()
    completed = []
    database_mutation_attempted = False
    try:
        _require_success(
            run([*compose, "stop", "api", "worker"], 60, release_values),
            "operator_migration_command_failed",
        )
        _require_volume_state(run, manifest, allowed_running={})

        _run_one_shot(
            run=run,
            compose=compose,
            manifest=manifest,
            manifest_path=manifest_path,
            release_values=release_values,
            service="operator-disposable-marker",
            created_ids=created_ids,
        )
        completed.append("disposable-target-certified")
        _require_volume_state(run, manifest, allowed_running={})

        for service, phase in (
            ("startup-migrator", "002-007"),
            ("operator-phase2b-migrator", "008"),
        ):
            database_mutation_attempted = True
            _run_one_shot(
                run=run,
                compose=compose,
                manifest=manifest,
                manifest_path=manifest_path,
                release_values=release_values,
                service=service,
                created_ids=created_ids,
            )
            completed.append(phase)
            _require_volume_state(run, manifest, allowed_running={})

        _run_worker_drain(
            run=run,
            compose=compose,
            manifest=manifest,
            manifest_path=manifest_path,
            release_values=release_values,
            created_ids=created_ids,
            drain_timeout_seconds=drain_timeout_seconds,
        )
        completed.append("008-worker-drain")
        _require_volume_state(run, manifest, allowed_running={})

        for service, phase in (
            ("phase2c-migrator-dry-run", "009-dry-run"),
            ("phase2c-migrator-apply", "009-apply"),
        ):
            _run_one_shot(
                run=run,
                compose=compose,
                manifest=manifest,
                manifest_path=manifest_path,
                release_values=release_values,
                service=service,
                created_ids=created_ids,
            )
            completed.append(phase)
            _require_volume_state(run, manifest, allowed_running={})
        _run_one_shot(
            run=run,
            compose=compose,
            manifest=manifest,
            manifest_path=manifest_path,
            release_values=release_values,
            service="detector-v2-preflight",
            created_ids=created_ids,
        )
        completed.append("010-read-only-preflight")
        _require_volume_state(run, manifest, allowed_running={})
    except (OperatorReleaseManifestError, OperatorReleaseOrchestrationError) as exc:
        _cleanup_created(run, created_ids, release_values)
        _stop_services(run, compose, release_values)
        try:
            _require_volume_state(run, manifest, allowed_running={})
        except OperatorReleaseOrchestrationError as exc:
            raise OperatorReleaseOrchestrationError(
                "operator_migration_unsafe_stop_unconfirmed",
                restore_required=database_mutation_attempted,
                services_stopped=False,
                last_committed_version=None,
            ) from exc
        last_committed = _read_actual_failure_identity(
            run=run,
            compose=compose,
            manifest=manifest,
            manifest_path=manifest_path,
            release_values=release_values,
            created_ids=created_ids,
            required=database_mutation_attempted,
        )
        raise OperatorReleaseOrchestrationError(
            exc.code,
            restore_required=database_mutation_attempted,
            services_stopped=True,
            last_committed_version=last_committed,
        ) from exc
    except KeyboardInterrupt as exc:
        _cleanup_created(run, created_ids, release_values)
        _stop_services(run, compose, release_values)
        try:
            _require_volume_state(run, manifest, allowed_running={})
        except OperatorReleaseOrchestrationError as stop_exc:
            raise OperatorReleaseOrchestrationError(
                "operator_migration_unsafe_stop_unconfirmed",
                restore_required=database_mutation_attempted,
                services_stopped=False,
                last_committed_version=None,
            ) from stop_exc
        last_committed = _read_actual_failure_identity(
            run=run,
            compose=compose,
            manifest=manifest,
            manifest_path=manifest_path,
            release_values=release_values,
            created_ids=created_ids,
            required=database_mutation_attempted,
        )
        raise OperatorReleaseOrchestrationError(
            "operator_migration_interrupted",
            restore_required=database_mutation_attempted,
            services_stopped=True,
            last_committed_version=last_committed,
        ) from exc
    except Exception as exc:
        _cleanup_created(run, created_ids, release_values)
        _stop_services(run, compose, release_values)
        try:
            _require_volume_state(run, manifest, allowed_running={})
        except OperatorReleaseOrchestrationError as stop_exc:
            raise OperatorReleaseOrchestrationError(
                "operator_migration_unsafe_stop_unconfirmed",
                restore_required=database_mutation_attempted,
                services_stopped=False,
                last_committed_version=None,
            ) from stop_exc
        last_committed = _read_actual_failure_identity(
            run=run,
            compose=compose,
            manifest=manifest,
            manifest_path=manifest_path,
            release_values=release_values,
            created_ids=created_ids,
            required=database_mutation_attempted,
        )
        raise OperatorReleaseOrchestrationError(
            "operator_migration_command_failed",
            restore_required=database_mutation_attempted,
            services_stopped=True,
            last_committed_version=last_committed,
        ) from exc

    return OperatorReleaseResult(
        status="migration_009_applied_preflight_verified_services_stopped",
        completed_phases=tuple(completed),
        services_stopped=True,
    )


def validate_rollback_reconstruction(
    *,
    manifest_path: Path,
    command_runner: CommandRunner | None = None,
) -> tuple[str, ...]:
    """Create, inspect, and remove stopped rollback containers without starting them."""
    manifest = load_manifest(manifest_path)
    run = command_runner or _run_command
    _require_disposable_volume(run, manifest)
    identity, rollback_values = load_env_source(
        manifest_path.parent / manifest.rollback_env.filename
    )
    _require_identity(identity, manifest.rollback_env)
    _require_manifest_volume_environment(manifest, rollback_values)
    required = {"USER_LUT_ROOT", "MEDIA_ROOT_HOST_PATH", "USER_LUT_ROOT_HOST_PATH"}
    if not required.issubset(rollback_values):
        raise OperatorReleaseOrchestrationError(
            "operator_migration_environment_mismatch"
        )
    created_ids: set[str] = set()
    checked = []
    try:
        for service in ("api", "worker"):
            image_id = manifest.rollback_image_ids[service]
            image_environment = _load_image_environment(run, image_id, rollback_values)
            container_name = (
                f"mediavault-rollback-{manifest.disposable_nonce}-{service}"
            )
            argv = [
                "docker",
                "create",
                "--name",
                container_name,
                "--label",
                f"com.docker.compose.project={manifest.compose_project}",
                "--label",
                f"com.docker.compose.service={service}",
                "--read-only",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=volume,src={manifest.database_volume},dst=/data,readonly",
                "--mount",
                (
                    "type=bind,src="
                    f"{rollback_values['MEDIA_ROOT_HOST_PATH']},dst=/media_root,readonly"
                ),
                "--mount",
                (
                    "type=bind,src="
                    f"{rollback_values['USER_LUT_ROOT_HOST_PATH']},dst=/user_luts,readonly"
                ),
            ]
            for key in sorted(_runtime_container_env(rollback_values)):
                argv.extend(("--env", key))
            argv.extend((image_id, *ROLLBACK_SERVICE_COMMANDS[service]))
            container_id = _require_success(
                run(argv, 60, rollback_values),
                "operator_migration_rollback_reconstruction_failed",
            ).stdout.strip()
            if not container_id or "\n" in container_id:
                raise OperatorReleaseOrchestrationError(
                    "operator_migration_container_invalid"
                )
            created_ids.add(container_id)
            snapshot = _inspect_container(run, container_id, rollback_values)
            _verify_rollback_container(
                snapshot,
                manifest=manifest,
                rollback_values=rollback_values,
                service=service,
                image_environment=image_environment,
            )
            _remove_created(run, container_id, rollback_values)
            created_ids.discard(container_id)
            checked.append(service)
    finally:
        if created_ids:
            _cleanup_created(run, created_ids, rollback_values)
    return tuple(checked)


def _verify_rollback_container(
    snapshot: ContainerSnapshot,
    *,
    manifest: OperatorReleaseManifest,
    rollback_values: Mapping[str, str],
    service: str,
    image_environment: Mapping[str, str],
) -> None:
    if (
        snapshot.service != service
        or snapshot.compose_project != manifest.compose_project
        or snapshot.state != "created"
        or snapshot.image_id != manifest.rollback_image_ids[service]
        or snapshot.command != ROLLBACK_SERVICE_COMMANDS[service]
        or snapshot.entrypoint
    ):
        raise OperatorReleaseOrchestrationError(
            "operator_migration_rollback_reconstruction_failed"
        )
    if not _host_config_is_hardened(snapshot.host_config, tmpfs_size_mib=None):
        raise OperatorReleaseOrchestrationError(
            "operator_migration_rollback_reconstruction_failed"
        )
    expected_env = _runtime_container_env(rollback_values)
    _require_exact_controlled_environment(
        snapshot.environment, expected_env, image_environment
    )
    expected_mounts = {
        ("volume", manifest.database_volume, "/data", False),
        ("bind", rollback_values["MEDIA_ROOT_HOST_PATH"], "/media_root", False),
        ("bind", rollback_values["USER_LUT_ROOT_HOST_PATH"], "/user_luts", False),
    }
    actual_mounts = _persistent_mount_contract(snapshot.mounts)
    if actual_mounts != expected_mounts:
        raise OperatorReleaseOrchestrationError(
            "operator_migration_rollback_reconstruction_failed"
        )


def _runtime_container_env(values: Mapping[str, str]) -> dict[str, str]:
    excluded = {
        "OPERATOR_DISPOSABLE_DATABASE_VOLUME",
        "OPERATOR_DISPOSABLE_NONCE",
        "MEDIA_ROOT_HOST_PATH",
        "USER_LUT_ROOT_HOST_PATH",
    }
    return {key: value for key, value in values.items() if key not in excluded}


def _require_manifest_volume_environment(
    manifest: OperatorReleaseManifest, values: Mapping[str, str]
) -> None:
    if (
        values.get("OPERATOR_DISPOSABLE_DATABASE_VOLUME") != manifest.database_volume
        or values.get("OPERATOR_DISPOSABLE_NONCE") != manifest.disposable_nonce
    ):
        raise OperatorReleaseOrchestrationError(
            "operator_migration_database_volume_mismatch"
        )


def _claim_operation(manifest_path: Path, manifest: OperatorReleaseManifest) -> None:
    claim = manifest_path.parent / f"{manifest_path.name}.operation-claim.json"
    payload = json.dumps(
        {
            "commit": manifest.commit,
            "database_volume": manifest.database_volume,
            "disposable_nonce": manifest.disposable_nonce,
            "status": "claimed_no_automatic_resume",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        descriptor = os.open(
            claim,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise OperatorReleaseOrchestrationError(
            "operator_migration_operation_already_claimed"
        ) from exc
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _run_one_shot(
    *,
    run: CommandRunner,
    compose: list[str],
    manifest: OperatorReleaseManifest,
    manifest_path: Path,
    release_values: Mapping[str, str],
    service: str,
    created_ids: set[str],
    capture_output: bool = False,
) -> str:
    container_id = _create_and_verify(
        run=run,
        compose=compose,
        manifest=manifest,
        manifest_path=manifest_path,
        release_values=release_values,
        service=service,
        created_ids=created_ids,
    )
    _require_volume_state(
        run,
        manifest,
        allowed_running={},
        allowed_created={service: container_id},
    )
    _require_success(
        run(["docker", "start", container_id], 60, release_values),
        "operator_migration_command_failed",
    )
    deadline = time.monotonic() + 900
    while True:
        snapshot = _inspect_container(run, container_id, release_values)
        if snapshot.state == "exited":
            if snapshot.exit_code != 0:
                raise OperatorReleaseOrchestrationError(
                    "operator_migration_command_failed"
                )
            break
        if snapshot.state != "running":
            raise OperatorReleaseOrchestrationError(
                "operator_migration_container_invalid"
            )
        _require_volume_state(run, manifest, allowed_running={service: container_id})
        if time.monotonic() >= deadline:
            raise OperatorReleaseOrchestrationError("operator_migration_command_failed")
        time.sleep(1)
    output = ""
    if capture_output:
        output = _require_success(
            run(["docker", "logs", container_id], 30, release_values),
            "operator_migration_identity_invalid",
        ).stdout.strip()
    _remove_created(run, container_id, release_values)
    created_ids.discard(container_id)
    return output


def _read_actual_failure_identity(
    *,
    run: CommandRunner,
    compose: list[str],
    manifest: OperatorReleaseManifest,
    manifest_path: Path,
    release_values: Mapping[str, str],
    created_ids: set[str],
    required: bool,
) -> str | None:
    if not required:
        return None
    try:
        output = _run_one_shot(
            run=run,
            compose=compose,
            manifest=manifest,
            manifest_path=manifest_path,
            release_values=release_values,
            service="operator-migration-identity",
            created_ids=created_ids,
            capture_output=True,
        )
        payload = json.loads(output)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"status", "last_committed_version", "migration_count"}
            or payload["status"] != "identity_verified"
            or not isinstance(payload["last_committed_version"], str)
            or type(payload["migration_count"]) is not int
            or payload["migration_count"] <= 0
        ):
            raise ValueError
        _require_volume_state(run, manifest, allowed_running={})
        return payload["last_committed_version"]
    except (
        OperatorReleaseManifestError,
        OperatorReleaseOrchestrationError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        _cleanup_created(run, created_ids, release_values)
        try:
            _require_volume_state(run, manifest, allowed_running={})
        except OperatorReleaseOrchestrationError as stop_exc:
            raise OperatorReleaseOrchestrationError(
                "operator_migration_unsafe_stop_unconfirmed",
                restore_required=required,
                services_stopped=False,
                last_committed_version=None,
            ) from stop_exc
        return None


def _run_worker_drain(
    *,
    run: CommandRunner,
    compose: list[str],
    manifest: OperatorReleaseManifest,
    manifest_path: Path,
    release_values: Mapping[str, str],
    created_ids: set[str],
    drain_timeout_seconds: int,
) -> None:
    service = "operator-worker-drain"
    container_id = _create_and_verify(
        run=run,
        compose=compose,
        manifest=manifest,
        manifest_path=manifest_path,
        release_values=release_values,
        service=service,
        created_ids=created_ids,
    )
    _require_volume_state(
        run,
        manifest,
        allowed_running={},
        allowed_created={service: container_id},
    )
    _require_success(
        run(["docker", "start", container_id], 60, release_values),
        "operator_migration_command_failed",
    )
    deadline = time.monotonic() + drain_timeout_seconds
    while True:
        _require_volume_state(
            run,
            manifest,
            allowed_running={service: container_id},
        )
        drained = run(
            [
                "docker",
                "exec",
                container_id,
                "uv",
                "run",
                "--frozen",
                "--no-dev",
                "python",
                "-m",
                "app.services.phase2b_drain_check",
            ],
            30,
            release_values,
        )
        if drained.returncode == 0 and drained.stdout.strip() == "drained":
            break
        if time.monotonic() >= deadline:
            raise OperatorReleaseOrchestrationError(
                "operator_migration_worker_drain_timeout"
            )
        time.sleep(1)
    _require_success(
        run(["docker", "stop", "--time", "30", container_id], 60, release_values),
        "operator_migration_command_failed",
    )
    _remove_created(run, container_id, release_values)
    created_ids.discard(container_id)


def _create_and_verify(
    *,
    run: CommandRunner,
    compose: list[str],
    manifest: OperatorReleaseManifest,
    manifest_path: Path,
    release_values: Mapping[str, str],
    service: str,
    created_ids: set[str],
) -> str:
    profile = SERVICE_PROFILES[service]
    _require_success(
        run(
            [
                *compose,
                "--profile",
                profile,
                "create",
                "--no-build",
                "--pull",
                "never",
                service,
            ],
            120,
            release_values,
        ),
        "operator_migration_command_failed",
    )
    container_id = _require_success(
        run([*compose, "ps", "-q", service], 30, release_values),
        "operator_migration_command_failed",
    ).stdout.strip()
    if not container_id or "\n" in container_id:
        raise OperatorReleaseOrchestrationError("operator_migration_container_invalid")
    created_ids.add(container_id)
    snapshot = _inspect_container(run, container_id, release_values)
    image_environment = _load_image_environment(
        run, manifest.release_image_ids[service], release_values
    )
    _verify_created_container(
        snapshot,
        manifest,
        release_values,
        service,
        image_environment,
    )
    current_identity, current_values = load_env_source(
        manifest_path.parent / manifest.release_env.filename
    )
    _require_identity(current_identity, manifest.release_env)
    if current_values != dict(release_values):
        raise OperatorReleaseOrchestrationError(
            "operator_migration_environment_mismatch"
        )
    return container_id


def _verify_created_container(
    snapshot: ContainerSnapshot,
    manifest: OperatorReleaseManifest,
    release_values: Mapping[str, str],
    service: str,
    image_environment: Mapping[str, str],
) -> None:
    if (
        snapshot.service != service
        or snapshot.compose_project != manifest.compose_project
        or snapshot.state not in {"created", "exited"}
        or snapshot.command != FIXED_SERVICE_COMMANDS[service]
        or snapshot.entrypoint
    ):
        raise OperatorReleaseOrchestrationError("operator_migration_container_invalid")
    if snapshot.image_id != manifest.release_image_ids[service]:
        raise OperatorReleaseOrchestrationError("operator_migration_artifact_mismatch")
    tmpfs_size_mib = 32 if service == "operator-worker-drain" else 16
    if not _host_config_is_hardened(
        snapshot.host_config, tmpfs_size_mib=tmpfs_size_mib
    ):
        raise OperatorReleaseOrchestrationError("operator_migration_container_invalid")
    expected_database_rw = service not in {
        "detector-v2-preflight",
        "operator-migration-identity",
    }
    expected_mounts = {
        ("volume", manifest.database_volume, "/data", expected_database_rw)
    }
    if service == "operator-worker-drain":
        expected_mounts.update(
            {
                (
                    "bind",
                    release_values["MEDIA_ROOT_HOST_PATH"],
                    "/media_root",
                    True,
                ),
                (
                    "bind",
                    release_values["USER_LUT_ROOT_HOST_PATH"],
                    "/user_luts",
                    False,
                ),
            }
        )
    elif service == "detector-v2-preflight":
        expected_mounts.add(
            (
                "bind",
                release_values["USER_LUT_ROOT_HOST_PATH"],
                "/user_luts",
                False,
            )
        )
    if _persistent_mount_contract(snapshot.mounts) != expected_mounts:
        raise OperatorReleaseOrchestrationError(
            "operator_migration_database_volume_mismatch"
        )
    expected_env = _expected_service_env(service, release_values)
    _require_exact_controlled_environment(
        snapshot.environment, expected_env, image_environment
    )


def _expected_service_env(
    service: str, release_values: Mapping[str, str]
) -> dict[str, str]:
    if service in FIXED_MIGRATOR_ENV:
        values = dict(FIXED_MIGRATOR_ENV[service])
        values.update(_disposable_target_env(release_values))
        return values
    if service in {
        "operator-disposable-marker",
        "operator-migration-identity",
        "startup-migrator",
    }:
        return {
            "DATABASE_PATH": release_values["DATABASE_PATH"],
            **_disposable_target_env(release_values),
        }
    if service == "operator-worker-drain":
        keys = {"API_TOKEN", "DATABASE_PATH", "MEDIA_ROOT", "USER_LUT_ROOT"}
        keys.update(
            key
            for key in ("SQLITE_BUSY_TIMEOUT_MS", "JOB_LEASE_SECONDS")
            if key in release_values
        )
        return {
            **{key: release_values[key] for key in keys},
            **_disposable_target_env(release_values),
        }
    if service == "detector-v2-preflight":
        return {
            "API_TOKEN": "migration-not-exposed",
            "DATABASE_PATH": release_values["DATABASE_PATH"],
            "MEDIA_ROOT": "/unmounted-media",
            "USER_LUT_ROOT": "/user_luts",
            **_disposable_target_env(release_values),
        }
    raise OperatorReleaseOrchestrationError("operator_migration_container_invalid")


def _disposable_target_env(values: Mapping[str, str]) -> dict[str, str]:
    return {
        "OPERATOR_DATABASE_VOLUME_NAME": values["OPERATOR_DISPOSABLE_DATABASE_VOLUME"],
        "OPERATOR_DISPOSABLE_NONCE": values["OPERATOR_DISPOSABLE_NONCE"],
    }


def _require_exact_controlled_environment(
    actual: Mapping[str, str],
    expected: Mapping[str, str],
    image_environment: Mapping[str, str],
) -> None:
    merged = {**image_environment, **expected}
    if dict(actual) != merged:
        raise OperatorReleaseOrchestrationError(
            "operator_migration_environment_mismatch"
        )


def _load_image_environment(
    run: CommandRunner,
    image_id: str,
    environment: Mapping[str, str] | None,
) -> dict[str, str]:
    raw = _require_success(
        run(["docker", "image", "inspect", image_id], 30, environment),
        "operator_migration_artifact_mismatch",
    ).stdout
    try:
        payload = json.loads(raw)
        if not isinstance(payload, list) or len(payload) != 1:
            raise ValueError
        values = payload[0]["Config"].get("Env") or []
        result = {}
        for value in values:
            key, separator, content = str(value).partition("=")
            if not separator or key in result:
                raise ValueError
            result[key] = content
        return result
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OperatorReleaseOrchestrationError(
            "operator_migration_artifact_mismatch"
        ) from exc


def _persistent_mount_contract(mounts: tuple[dict, ...]) -> set[tuple]:
    result = set()
    for mount in mounts:
        mount_type = str(mount.get("Type"))
        if mount_type == "tmpfs" and mount.get("Destination") == "/tmp":
            continue
        if mount_type not in {"bind", "volume"}:
            raise OperatorReleaseOrchestrationError(
                "operator_migration_container_invalid"
            )
        result.add(
            (
                mount_type,
                str(mount.get("Name") or mount.get("Source")),
                str(mount.get("Destination")),
                bool(mount.get("RW")),
            )
        )
    return result


def _host_config_is_hardened(host: Mapping, *, tmpfs_size_mib: int | None) -> bool:
    security = set(host.get("SecurityOpt") or [])
    caps = {str(value).upper() for value in (host.get("CapDrop") or [])}
    restart = host.get("RestartPolicy") or {}
    tmpfs = host.get("Tmpfs") or {}
    if tmpfs_size_mib is None:
        tmpfs_valid = not tmpfs
    else:
        options = set(str(tmpfs.get("/tmp", "")).split(","))
        size_values = {
            f"size={tmpfs_size_mib}m",
            f"size={tmpfs_size_mib * 1024}k",
            f"size={tmpfs_size_mib * 1024 * 1024}",
        }
        tmpfs_valid = set(tmpfs) == {"/tmp"} and any(
            options == {"rw", "noexec", "nosuid", "nodev", size} for size in size_values
        )
    return (
        host.get("ReadonlyRootfs") is True
        and host.get("NetworkMode") == "none"
        and caps == {"ALL"}
        and security == {"no-new-privileges:true"}
        and host.get("Privileged") in {None, False}
        and not (host.get("Devices") or [])
        and host.get("PidMode") in {None, "", "private"}
        and host.get("IpcMode") in {None, "", "private"}
        and host.get("UTSMode") in {None, "", "private"}
        and restart.get("Name", "no") in {"", "no"}
        and int(restart.get("MaximumRetryCount", 0)) == 0
        and tmpfs_valid
    )


def _require_volume_state(
    run: CommandRunner,
    manifest: OperatorReleaseManifest,
    *,
    allowed_running: Mapping[str, str],
    allowed_created: Mapping[str, str] | None = None,
) -> None:
    ids = _require_success(
        run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"volume={manifest.database_volume}",
                "--format",
                "{{.ID}}",
            ],
            30,
            None,
        ),
        "operator_migration_volume_container_query_failed",
    ).stdout.splitlines()
    allowed_created = allowed_created or {}
    running: dict[str, str] = {}
    created: dict[str, str] = {}
    seen_services: set[str] = set()
    allowed_present = (
        KNOWN_STOPPED_SERVICES | set(allowed_running) | set(allowed_created)
    )
    for container_id in (value.strip() for value in ids if value.strip()):
        snapshot = _inspect_container(run, container_id, None)
        if (
            snapshot.compose_project != manifest.compose_project
            or snapshot.service not in allowed_present
            or snapshot.service in seen_services
        ):
            raise OperatorReleaseOrchestrationError(
                "operator_migration_unexpected_volume_container"
            )
        seen_services.add(snapshot.service)
        if snapshot.state == "running":
            if (
                snapshot.service not in allowed_running
                or allowed_running[snapshot.service] != container_id
                or snapshot.image_id != manifest.release_image_ids[snapshot.service]
            ):
                raise OperatorReleaseOrchestrationError(
                    "operator_migration_unsafe_stop_unconfirmed"
                )
            running[snapshot.service] = container_id
        elif snapshot.state == "created" and snapshot.service in allowed_created:
            if (
                allowed_created[snapshot.service] != container_id
                or snapshot.image_id != manifest.release_image_ids[snapshot.service]
            ):
                raise OperatorReleaseOrchestrationError(
                    "operator_migration_unsafe_stop_unconfirmed"
                )
            created[snapshot.service] = container_id
        elif snapshot.state not in {"created", "exited"}:
            raise OperatorReleaseOrchestrationError(
                "operator_migration_unsafe_stop_unconfirmed"
            )
    if running != dict(allowed_running) or created != dict(allowed_created):
        raise OperatorReleaseOrchestrationError(
            "operator_migration_unsafe_stop_unconfirmed"
        )


def _require_disposable_volume(
    run: CommandRunner, manifest: OperatorReleaseManifest
) -> None:
    raw = _require_success(
        run(["docker", "volume", "inspect", manifest.database_volume], 30, None),
        "operator_migration_disposable_volume_invalid",
    ).stdout
    try:
        payload = json.loads(raw)
        if not isinstance(payload, list) or len(payload) != 1:
            raise ValueError
        item = payload[0]
        labels = item.get("Labels") or {}
        if (
            item.get("Name") != manifest.database_volume
            or labels.get("mediavault.disposable") != "true"
            or labels.get("mediavault.nonce") != manifest.disposable_nonce
        ):
            raise ValueError
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OperatorReleaseOrchestrationError(
            "operator_migration_disposable_volume_invalid"
        ) from exc


def _inspect_container(
    run: CommandRunner,
    container_id: str,
    environment: Mapping[str, str] | None,
) -> ContainerSnapshot:
    raw = _require_success(
        run(["docker", "inspect", container_id], 30, environment),
        "operator_migration_container_inspect_failed",
    ).stdout
    try:
        payload = json.loads(raw)
        if not isinstance(payload, list) or len(payload) != 1:
            raise ValueError
        item = payload[0]
        labels = item["Config"].get("Labels") or {}
        env = {}
        for value in item["Config"].get("Env") or []:
            key, separator, content = value.partition("=")
            if not separator or key in env:
                raise ValueError
            env[key] = content
        state = item["State"]
        return ContainerSnapshot(
            container_id=str(item["Id"]),
            service=str(labels.get("com.docker.compose.service", "")),
            compose_project=str(labels.get("com.docker.compose.project", "")),
            image_id=str(item["Image"]),
            state=str(state["Status"]),
            exit_code=int(state.get("ExitCode", 0)),
            environment=env,
            mounts=tuple(item.get("Mounts") or []),
            host_config=dict(item.get("HostConfig") or {}),
            command=tuple(str(value) for value in (item["Config"].get("Cmd") or [])),
            entrypoint=tuple(
                str(value) for value in (item["Config"].get("Entrypoint") or [])
            ),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OperatorReleaseOrchestrationError(
            "operator_migration_container_inspect_failed"
        ) from exc


def _cleanup_created(
    run: CommandRunner,
    created_ids: set[str],
    environment: Mapping[str, str],
) -> None:
    for container_id in tuple(created_ids):
        _best_effort_run(
            run,
            ["docker", "stop", "--time", "10", container_id],
            30,
            environment,
        )
        _best_effort_run(
            run,
            ["docker", "rm", "--force", container_id],
            30,
            environment,
        )
        created_ids.discard(container_id)


def _remove_created(
    run: CommandRunner,
    container_id: str,
    environment: Mapping[str, str],
) -> None:
    _require_success(
        run(["docker", "rm", container_id], 30, environment),
        "operator_migration_command_failed",
    )


def _stop_services(
    run: CommandRunner,
    compose: list[str],
    environment: Mapping[str, str],
) -> None:
    _best_effort_run(
        run,
        [*compose, "stop", "api", "worker"],
        60,
        environment,
    )


def _best_effort_run(
    run: CommandRunner,
    argv: list[str],
    timeout: int,
    environment: Mapping[str, str],
) -> bool:
    try:
        run(argv, timeout, environment)
    except Exception:  # noqa: BLE001 - final state is verified after cleanup.
        return False
    return True


def _require_identity(actual: EnvSourceIdentity, expected: EnvSourceIdentity) -> None:
    if actual != expected:
        raise OperatorReleaseOrchestrationError(
            "operator_migration_environment_mismatch"
        )


def _compose_argv(
    repository_root: Path,
    manifest: OperatorReleaseManifest,
) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(repository_root),
        "--file",
        str(repository_root / "docker-compose.yml"),
        "--project-name",
        manifest.compose_project,
    ]


def _require_success(result: HostCommandResult, code: str) -> HostCommandResult:
    if result.returncode != 0:
        raise OperatorReleaseOrchestrationError(code)
    return result


def _run_command(
    argv: list[str],
    timeout_seconds: int,
    environment: Mapping[str, str] | None,
) -> HostCommandResult:
    subprocess_environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "DOCKER_CONFIG", "LANG", "LC_ALL")
        if key in os.environ
    }
    if environment is not None:
        subprocess_environment.update(environment)
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
            env=subprocess_environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OperatorReleaseOrchestrationError(
            "operator_migration_command_failed"
        ) from exc
    if len(completed.stdout.encode("utf-8")) > 1_048_576:
        raise OperatorReleaseOrchestrationError("operator_migration_command_failed")
    return HostCommandResult(completed.returncode, completed.stdout)
