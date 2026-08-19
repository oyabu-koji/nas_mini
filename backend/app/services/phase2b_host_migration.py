from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


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
    del repository_root, command_runner, drain_timeout_seconds
    raise Phase2BHostMigrationError("legacy_operator_migration_wrapper_disabled")


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
