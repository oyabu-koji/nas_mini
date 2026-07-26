from app.services.phase2b_host_migration import (
    HostCommandResult,
    run_phase2b_host_migration,
)


def test_host_wrapper_stops_drains_migrates_then_starts_services(tmp_path):
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

    run_phase2b_host_migration(
        repository_root=tmp_path,
        command_runner=run,
    )

    commands = [" ".join(call[0]) for call in calls]
    assert commands[0].endswith("stop api")
    assert "phase2b_drain_check" in commands[2]
    assert commands[3].endswith("stop worker")
    assert "--profile phase2b-migration run --rm --no-deps -T phase2b-migrator" in commands[5]
    assert commands[6].endswith("up -d api worker")
