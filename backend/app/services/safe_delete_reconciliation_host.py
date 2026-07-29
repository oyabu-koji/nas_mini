from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal


@dataclass(frozen=True)
class HostCommandResult:
    returncode: int
    stdout: str


CommandRunner = Callable[[list[str], int], HostCommandResult]


class SafeDeleteReconciliationHostError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def run_safe_delete_reconciliation_host(
    *,
    repository_root: Path,
    mode: Literal["dry-run", "apply"],
    command_runner: CommandRunner | None = None,
) -> None:
    repository_root = repository_root.resolve()
    if mode not in {"dry-run", "apply"}:
        raise SafeDeleteReconciliationHostError(
            "phase2c_reconciliation_mode_invalid"
        )
    if not (repository_root / "docker-compose.yml").is_file():
        raise SafeDeleteReconciliationHostError(
            "phase2c_reconciliation_invalid_working_directory"
        )
    run = command_runner or _run_command
    result = run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(repository_root),
            "--profile",
            "phase2c-reconciliation",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "phase2c-reconciler",
            "uv",
            "run",
            "--frozen",
            "--no-dev",
            "python",
            "-m",
            "scripts.reconcile_safe_delete_candidates",
            f"--{mode}",
        ],
        900,
    )
    if result.returncode != 0:
        raise SafeDeleteReconciliationHostError(
            "phase2c_reconciliation_command_failed"
        )


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
        raise SafeDeleteReconciliationHostError(
            "phase2c_reconciliation_command_failed"
        ) from exc
    if len(completed.stdout.encode("utf-8")) > 1_048_576:
        raise SafeDeleteReconciliationHostError(
            "phase2c_reconciliation_command_failed"
        )
    return HostCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
    )
