from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class HostCommandResult:
    returncode: int
    stdout: str


CommandRunner = Callable[[list[str], int], HostCommandResult]


class Phase2BHostMigrationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def run_phase2b_host_migration(
    *,
    repository_root: Path,
    command_runner: CommandRunner | None = None,
    drain_timeout_seconds: int = 300,
) -> None:
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
            raise Phase2BHostMigrationError("phase2b_migration_preview_not_drained")
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
        raise Phase2BHostMigrationError("phase2b_migration_services_running")

    migration = run(
        [
            *compose,
            "--profile",
            "phase2b-migration",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "phase2b-migrator",
        ],
        900,
    )
    _require_success(migration)
    _require_success(run([*compose, "up", "-d", "api", "worker"], 300))


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
        raise Phase2BHostMigrationError("phase2b_migration_command_failed") from exc
    if len(completed.stdout.encode("utf-8")) > 1_048_576:
        raise Phase2BHostMigrationError("phase2b_migration_command_failed")
    return HostCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
    )


def _require_success(result: HostCommandResult) -> HostCommandResult:
    if result.returncode != 0:
        raise Phase2BHostMigrationError("phase2b_migration_command_failed")
    return result
