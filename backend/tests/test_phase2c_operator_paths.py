import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.services.phase2c_host_migration import (
    HostCommandResult as MigrationCommandResult,
)
from app.services.phase2c_host_migration import (
    Phase2CHostMigrationError,
    run_phase2c_host_migration,
)
from app.services.phase2c_host_migration import (
    _run_command as run_migration_command,
)
from app.services.phase2c_migration import Phase2CMigrationResult
from app.services.safe_delete_reconciliation import ReconciliationSummary
from app.services.safe_delete_reconciliation_host import (
    HostCommandResult as ReconciliationCommandResult,
)
from app.services.safe_delete_reconciliation_host import (
    SafeDeleteReconciliationHostError,
    run_safe_delete_reconciliation_host,
)
from app.services.safe_delete_reconciliation_host import (
    _run_command as run_reconciliation_command,
)

from scripts import migrate_phase2c_safe_delete_candidate as migration_cli
from scripts import reconcile_safe_delete_candidates as reconciliation_cli
from scripts import run_phase2c_safe_delete_candidate_migration as migration_host_cli
from scripts import run_safe_delete_candidate_reconciliation as reconciliation_host_cli

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("mode", ["dry-run", "apply"])
def test_phase2c_migration_legacy_host_wrapper_is_disabled(mode):
    calls = []

    def runner(argv, timeout):
        calls.append((argv, timeout))
        if "ps" in argv and "api" not in argv:
            return MigrationCommandResult(0, "worker\n")
        if "exec" in argv:
            return MigrationCommandResult(0, "drained")
        return MigrationCommandResult(0, "")

    with pytest.raises(
        Phase2CHostMigrationError,
        match="legacy_operator_migration_wrapper_disabled",
    ):
        run_phase2c_host_migration(
            repository_root=REPOSITORY_ROOT,
            mode=mode,
            command_runner=runner,
        )

    assert calls == []


def test_phase2c_migration_failure_keeps_services_stopped():
    calls = []

    def runner(argv, timeout):
        calls.append((argv, timeout))
        if "phase2c-migrator" in argv:
            return MigrationCommandResult(1, "phase2c_migration_failed")
        return MigrationCommandResult(0, "")

    with pytest.raises(
        Phase2CHostMigrationError,
        match="legacy_operator_migration_wrapper_disabled",
    ):
        run_phase2c_host_migration(
            repository_root=REPOSITORY_ROOT,
            mode="apply",
            command_runner=runner,
        )

    assert not any("up" in argv for argv, _timeout in calls)


@pytest.mark.parametrize("mode", ["dry-run", "apply"])
def test_reconciliation_host_wrapper_uses_one_networkless_service(mode):
    calls = []

    def runner(argv, timeout):
        calls.append((argv, timeout))
        return ReconciliationCommandResult(0, "")

    run_safe_delete_reconciliation_host(
        repository_root=REPOSITORY_ROOT,
        mode=mode,
        command_runner=runner,
    )

    assert len(calls) == 1
    argv, timeout = calls[0]
    assert argv[:5] == [
        "docker",
        "compose",
        "--project-directory",
        str(REPOSITORY_ROOT),
        "--profile",
    ]
    assert "phase2c-reconciler" in argv
    module_index = argv.index("-m")
    assert argv[module_index : module_index + 2] == [
        "-m",
        "scripts.reconcile_safe_delete_candidates",
    ]
    assert argv[-1] == f"--{mode}"
    assert timeout == 900


@pytest.mark.parametrize(
    ("runner", "error_type", "code"),
    [
        (
            run_migration_command,
            Phase2CHostMigrationError,
            "phase2c_migration_command_failed",
        ),
        (
            run_reconciliation_command,
            SafeDeleteReconciliationHostError,
            "phase2c_reconciliation_command_failed",
        ),
    ],
)
def test_operator_command_runner_rejects_unbounded_output(
    monkeypatch,
    runner,
    error_type,
    code,
):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="x" * 1_048_577,
        ),
    )

    with pytest.raises(error_type, match=code):
        runner(["fixed-command"], 7)


def test_operator_command_runner_disables_shell_and_bounds_timeout(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = run_migration_command(["fixed-command", "--apply"], 17)

    assert result == MigrationCommandResult(0, "ok")
    assert captured["argv"] == ["fixed-command", "--apply"]
    assert captured["shell"] is False
    assert captured["timeout"] == 17
    assert captured["check"] is False
    assert captured["stderr"] is not None


@pytest.mark.parametrize(
    ("runner", "error_type", "code"),
    [
        (
            run_phase2c_host_migration,
            Phase2CHostMigrationError,
            "phase2c_migration_mode_invalid",
        ),
        (
            run_safe_delete_reconciliation_host,
            SafeDeleteReconciliationHostError,
            "phase2c_reconciliation_mode_invalid",
        ),
    ],
)
def test_operator_host_wrapper_rejects_invalid_mode(runner, error_type, code):
    with pytest.raises(error_type, match=code):
        runner(
            repository_root=REPOSITORY_ROOT,
            mode="invalid",
            command_runner=lambda _argv, _timeout: None,
        )


def test_migration_cli_outputs_only_safe_aggregate(monkeypatch, capsys):
    monkeypatch.setattr(migration_cli, "load_settings", lambda: object())
    monkeypatch.setattr(
        migration_cli,
        "apply_phase2c_migration",
        lambda **_kwargs: Phase2CMigrationResult(
            status="dry_run",
            promoted=1,
            skipped=2,
            reasons={"preview_not_confirmed": 2},
            schema_sql_sha256="a" * 64,
            assets_table_sql_sha256="b" * 64,
        ),
    )

    assert migration_cli.main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert output == (
        '{"promoted":1,"reasons":{"preview_not_confirmed":2},'
        '"skipped":2,"status":"dry_run"}\n'
    )
    assert "a" * 64 not in output
    assert "b" * 64 not in output


def test_reconciliation_cli_outputs_only_safe_aggregate(monkeypatch, capsys):
    monkeypatch.setattr(reconciliation_cli, "load_settings", lambda: object())
    monkeypatch.setattr(
        reconciliation_cli,
        "reconcile_safe_delete_candidates",
        lambda **_kwargs: ReconciliationSummary(
            status="applied",
            examined=3,
            promoted=1,
            demoted=1,
            unchanged=1,
            reasons={"formal_preview_provenance_invalid": 1},
        ),
    )

    assert reconciliation_cli.main(["--apply"]) == 0
    output = capsys.readouterr().out
    assert output == (
        '{"demoted":1,"examined":3,"promoted":1,'
        '"reasons":{"formal_preview_provenance_invalid":1},'
        '"status":"applied","unchanged":1}\n'
    )


@pytest.mark.parametrize(
    ("module", "code"),
    [
        (migration_cli, "phase2c_migration_failed"),
        (reconciliation_cli, "phase2c_reconciliation_failed"),
    ],
)
def test_container_cli_hides_unexpected_exception_details(
    monkeypatch,
    capsys,
    module,
    code,
):
    monkeypatch.setattr(module, "load_settings", lambda: object())
    target = (
        "apply_phase2c_migration"
        if module is migration_cli
        else "reconcile_safe_delete_candidates"
    )
    monkeypatch.setattr(
        module,
        target,
        lambda **_kwargs: (_ for _ in ()).throw(
            ValueError("/secret/path/" + ("f" * 64))
        ),
    )

    assert module.main(["--dry-run"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{code}\n"


@pytest.mark.parametrize(
    ("module", "runner_name", "invalid_code", "success_text"),
    [
        (
            migration_host_cli,
            "run_phase2c_host_migration",
            "phase2c_migration_invalid_working_directory",
            "phase2c_migration_complete\n",
        ),
        (
            reconciliation_host_cli,
            "run_safe_delete_reconciliation_host",
            "phase2c_reconciliation_invalid_working_directory",
            "phase2c_reconciliation_complete\n",
        ),
    ],
)
def test_host_cli_requires_backend_cwd_and_explicit_mode(
    monkeypatch,
    capsys,
    module,
    runner_name,
    invalid_code,
    success_text,
):
    monkeypatch.setattr(module, runner_name, lambda **_kwargs: None)

    monkeypatch.chdir(REPOSITORY_ROOT)
    assert module.main(["--dry-run"]) == 2
    assert capsys.readouterr().err == f"{invalid_code}\n"

    monkeypatch.chdir(REPOSITORY_ROOT / "backend")
    assert module.main(["--dry-run"]) == 0
    assert capsys.readouterr().out == success_text


@pytest.mark.parametrize(
    "module_name",
    [
        "scripts.run_phase2c_safe_delete_candidate_migration",
        "scripts.run_safe_delete_candidate_reconciliation",
        "scripts.migrate_phase2c_safe_delete_candidate",
        "scripts.reconcile_safe_delete_candidates",
    ],
)
def test_operator_module_entrypoint_starts_in_a_real_process(module_name):
    completed = subprocess.run(
        [sys.executable, "-m", module_name, "--help"],
        cwd=REPOSITORY_ROOT / "backend",
        shell=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--help" in completed.stdout
