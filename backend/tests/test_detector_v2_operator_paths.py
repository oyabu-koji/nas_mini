import json
from pathlib import Path

import pytest
from app.services.detector_v2_host_migration import (
    DetectorV2HostMigrationError,
    HostCommandResult,
    run_detector_v2_host_migration,
)
from app.services.detector_v2_migration import DetectorV2MigrationResult

from scripts import check_detector_v2_api_capability as capability_cli
from scripts import migrate_detector_v2 as migration_cli

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_detector_v2_cli_defaults_to_preflight_only(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(migration_cli, "load_settings", lambda: object())

    def migrate(**kwargs):
        captured.update(kwargs)
        return DetectorV2MigrationResult(
            status="preflight_ready",
            schema_identity_sha256="a" * 64,
        )

    monkeypatch.setattr(migration_cli, "apply_detector_v2_migration", migrate)

    assert migration_cli.main([]) == 0
    assert captured["mode"] == "preflight-only"
    assert capsys.readouterr().out == '{"status":"preflight_ready"}\n'


def test_detector_v2_cli_passes_explicit_dry_run_confirmation(monkeypatch):
    captured = {}
    monkeypatch.setattr(migration_cli, "load_settings", lambda: object())

    def migrate(**kwargs):
        captured.update(kwargs)
        return DetectorV2MigrationResult(
            status="dry_run",
            schema_identity_sha256="a" * 64,
        )

    monkeypatch.setattr(migration_cli, "apply_detector_v2_migration", migrate)

    assert migration_cli.main(["--dry-run", "--isolated-database-confirmed"]) == 0
    assert captured["mode"] == "dry-run"
    assert captured["isolated_database_confirmed"] is True


def test_detector_v2_host_wrapper_stops_drains_migrates_then_restarts():
    calls = []

    def runner(argv, timeout):
        calls.append((argv, timeout))
        if "scripts.check_detector_v2_api_capability" in argv:
            return HostCommandResult(0, "detector_v2_capability_ready\n")
        if "ps" in argv and "api" not in argv:
            return HostCommandResult(0, "worker\n")
        if "exec" in argv:
            return HostCommandResult(0, "drained")
        return HostCommandResult(0, "")

    run_detector_v2_host_migration(
        repository_root=REPOSITORY_ROOT,
        command_runner=runner,
    )

    assert calls[0][0][-2:] == ["stop", "api"]
    assert any(
        any("phase2b_drain_check" in argument for argument in argv)
        for argv, _timeout in calls
    )
    assert any(argv[-2:] == ["stop", "worker"] for argv, _timeout in calls)
    migration = next(argv for argv, _timeout in calls if "detector-v2-migrator" in argv)
    assert migration[-4:] == [
        "--apply",
        "--offline-maintenance-confirmed",
        "--api-stopped-confirmed",
        "--release-040-ready-confirmed",
    ]
    assert any(argv[-4:] == ["up", "-d", "api", "worker"] for argv, _timeout in calls)
    assert calls[-1][0][-2:] == [
        "-m",
        "scripts.check_detector_v2_api_capability",
    ]


def test_detector_v2_host_failure_keeps_services_stopped():
    calls = []

    def runner(argv, timeout):
        calls.append((argv, timeout))
        if "detector-v2-migrator" in argv:
            return HostCommandResult(1, "failed")
        return HostCommandResult(0, "")

    with pytest.raises(
        DetectorV2HostMigrationError,
        match="detector_v2_migration_command_failed",
    ):
        run_detector_v2_host_migration(
            repository_root=REPOSITORY_ROOT,
            command_runner=runner,
        )

    assert not any("up" in argv for argv, _timeout in calls)


def test_detector_v2_post_start_failure_stops_services():
    calls = []

    def runner(argv, timeout):
        calls.append((argv, timeout))
        if "scripts.check_detector_v2_api_capability" in argv:
            return HostCommandResult(1, "")
        return HostCommandResult(0, "")

    with pytest.raises(
        DetectorV2HostMigrationError,
        match="detector_v2_post_start_capability_unavailable",
    ):
        run_detector_v2_host_migration(
            repository_root=REPOSITORY_ROOT,
            command_runner=runner,
            post_start_timeout_seconds=0,
        )

    assert calls[-1][0][-3:] == ["stop", "api", "worker"]


def test_detector_v2_post_start_command_error_stops_services():
    calls = []

    def runner(argv, timeout):
        calls.append((argv, timeout))
        if "scripts.check_detector_v2_api_capability" in argv:
            raise DetectorV2HostMigrationError("detector_v2_migration_command_failed")
        return HostCommandResult(0, "")

    with pytest.raises(
        DetectorV2HostMigrationError,
        match="detector_v2_migration_command_failed",
    ):
        run_detector_v2_host_migration(
            repository_root=REPOSITORY_ROOT,
            command_runner=runner,
        )

    assert calls[-1][0][-3:] == ["stop", "api", "worker"]


def test_detector_v2_capability_check_accepts_ready_response(monkeypatch, capsys):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return json.dumps(
                {
                    "minimum_client_version": "0.4.0",
                    "features": {
                        "detector_certified": True,
                        "formal_apple_log_preview": True,
                        "safe_delete_candidate": True,
                    },
                }
            ).encode()

    monkeypatch.setenv("API_TOKEN", "test-token")
    monkeypatch.setattr(
        capability_cli.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    assert capability_cli.main() == 0
    assert capsys.readouterr().out == "detector_v2_capability_ready\n"


def test_detector_v2_capability_check_rejects_non_object_response(monkeypatch, capsys):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return b"[]"

    monkeypatch.setenv("API_TOKEN", "test-token")
    monkeypatch.setattr(
        capability_cli.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    assert capability_cli.main() == 1
    assert capsys.readouterr().err == "detector_v2_post_start_capability_unavailable\n"
