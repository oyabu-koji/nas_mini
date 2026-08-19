import pytest
from app.services.phase2b_host_migration import (
    HostCommandResult,
    Phase2BHostMigrationError,
    run_phase2b_host_migration,
)


def test_legacy_host_wrapper_is_disabled_before_any_command(tmp_path):
    calls = []

    def run(argv, timeout):
        calls.append((argv, timeout))
        joined = " ".join(argv)
        if "ps --status running --services worker" in joined:
            return HostCommandResult(0, "worker\n")
        if "phase2b_drain_check" in joined:
            return HostCommandResult(0, "drained\n")
        if "ps --status running --services api worker" in joined:
            return HostCommandResult(0, "")
        return HostCommandResult(0, "")

    with pytest.raises(
        Phase2BHostMigrationError,
        match="legacy_operator_migration_wrapper_disabled",
    ):
        run_phase2b_host_migration(
            repository_root=tmp_path,
            command_runner=run,
        )

    assert calls == []
