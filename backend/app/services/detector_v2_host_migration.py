from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HostCommandResult:
    returncode: int
    stdout: str


CommandRunner = Callable[[list[str], int], HostCommandResult]


class DetectorV2HostMigrationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def run_detector_v2_host_migration(
    *,
    repository_root: Path,
    command_runner: CommandRunner | None = None,
    drain_timeout_seconds: int = 300,
    post_start_timeout_seconds: int = 120,
) -> None:
    repository_root = repository_root.resolve()
    if not (repository_root / "docker-compose.yml").is_file():
        raise DetectorV2HostMigrationError(
            "detector_v2_migration_invalid_working_directory"
        )
    run = command_runner or _run_command
    compose = ["docker", "compose", "--project-directory", str(repository_root)]
    _require_success(run([*compose, "stop", "api"], 60))
    worker_state = _require_success(
        run([*compose, "ps", "--status", "running", "--services", "worker"], 30)
    )
    deadline = time.monotonic() + drain_timeout_seconds
    while worker_state.stdout.strip():
        drained = run(
            [
                *compose,
                "exec",
                "-T",
                "worker",
                "uv",
                "run",
                "--frozen",
                "--no-dev",
                "python",
                "-m",
                "app.services.phase2b_drain_check",
            ],
            30,
        )
        if drained.returncode == 0 and drained.stdout.strip() == "drained":
            break
        if time.monotonic() >= deadline:
            raise DetectorV2HostMigrationError(
                "detector_v2_migration_preview_not_drained"
            )
        time.sleep(2)
    _require_success(run([*compose, "stop", "worker"], 60))
    running = _require_success(
        run(
            [
                *compose,
                "ps",
                "--status",
                "running",
                "--services",
                "api",
                "worker",
            ],
            30,
        )
    )
    if running.stdout.strip():
        raise DetectorV2HostMigrationError("detector_v2_migration_services_running")
    migration = run(
        [
            *compose,
            "--profile",
            "detector-v2-migration",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "detector-v2-migrator",
            "uv",
            "run",
            "--frozen",
            "--no-dev",
            "python",
            "-m",
            "scripts.migrate_detector_v2",
            "--apply",
            "--offline-maintenance-confirmed",
            "--api-stopped-confirmed",
            "--release-040-ready-confirmed",
        ],
        900,
    )
    _require_success(migration)
    _require_success(run([*compose, "up", "-d", "api", "worker"], 300))
    deadline = time.monotonic() + post_start_timeout_seconds
    while True:
        try:
            capability = run(
                [
                    *compose,
                    "exec",
                    "-T",
                    "api",
                    "uv",
                    "run",
                    "--frozen",
                    "--no-dev",
                    "python",
                    "-m",
                    "scripts.check_detector_v2_api_capability",
                ],
                15,
            )
        except DetectorV2HostMigrationError:
            _require_success(run([*compose, "stop", "api", "worker"], 60))
            raise
        if (
            capability.returncode == 0
            and capability.stdout.strip() == "detector_v2_capability_ready"
        ):
            return
        if time.monotonic() >= deadline:
            _require_success(run([*compose, "stop", "api", "worker"], 60))
            raise DetectorV2HostMigrationError(
                "detector_v2_post_start_capability_unavailable"
            )
        time.sleep(2)


def _run_command(argv: list[str], timeout_seconds: int) -> HostCommandResult:
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
        raise DetectorV2HostMigrationError(
            "detector_v2_migration_command_failed"
        ) from exc
    if len(completed.stdout.encode("utf-8")) > 1_048_576:
        raise DetectorV2HostMigrationError("detector_v2_migration_command_failed")
    return HostCommandResult(completed.returncode, completed.stdout)


def _require_success(result: HostCommandResult) -> HostCommandResult:
    if result.returncode != 0:
        raise DetectorV2HostMigrationError("detector_v2_migration_command_failed")
    return result
