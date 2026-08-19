import json
import subprocess

import pytest
from app.services.operator_release_manifest import (
    REQUIRED_IMAGE_SERVICES,
    OperatorReleaseManifestError,
    load_manifest,
    write_env_source,
    write_manifest,
)
from app.services.operator_release_orchestration import (
    FIXED_SERVICE_COMMANDS,
    ROLLBACK_SERVICE_COMMANDS,
    HostCommandResult,
    OperatorReleaseOrchestrationError,
    _require_volume_state,
    _run_command,
    run_operator_release_orchestration,
    validate_rollback_reconstruction,
)

IMAGE_ID = f"sha256:{'a' * 64}"


def _env():
    return {
        "API_TOKEN": "test-token",
        "DATABASE_PATH": "/data/mediavault.sqlite3",
        "OPERATOR_DISPOSABLE_DATABASE_VOLUME": "disposable-test_backend-db",
        "OPERATOR_DISPOSABLE_NONCE": "test-disposable-0001",
        "MEDIA_ROOT": "/media_root",
        "USER_LUT_ROOT": "/user_luts",
        "MEDIA_ROOT_HOST_PATH": "/private/tmp/disposable-media",
        "USER_LUT_ROOT_HOST_PATH": "/private/tmp/disposable-user-luts",
        "SQLITE_BUSY_TIMEOUT_MS": "5000",
        "JOB_LEASE_SECONDS": "300",
    }


def _manifest(tmp_path, volume="disposable-test_backend-db"):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    release = tmp_path / "release.json"
    rollback = tmp_path / "rollback.json"
    write_env_source(release, _env())
    write_env_source(rollback, {**_env(), "API_TOKEN": "rollback-token"})
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        commit="a" * 40,
        compose_project="disposable-test",
        database_volume=volume,
        disposable_nonce="test-disposable-0001",
        database_path="/data/mediavault.sqlite3",
        release_image_ids={service: IMAGE_ID for service in REQUIRED_IMAGE_SERVICES},
        rollback_image_ids={service: IMAGE_ID for service in REQUIRED_IMAGE_SERVICES},
        release_env_source=release,
        rollback_env_source=rollback,
    )
    return manifest


class FakeDocker:
    def __init__(self, volume="disposable-test_backend-db"):
        self.volume = volume
        self.calls = []
        self.containers = {}
        self.fail_service = None
        self.unknown_container = False
        self.image_id = IMAGE_ID
        self.compose_stop_failure = False
        self.worker_start_failure = False
        self.commit = "a" * 40
        self.disposable_label = "true"
        self.base_image_env = {}
        self.database_version = "001_initial"
        self.post_commit_failure_service = None
        self.failure_version = None
        self.dirty_contract = False
        self.identity_payload = None
        self.identity_cleanup_failure = False

    def __call__(self, argv, timeout, environment=None):
        self.calls.append((argv, timeout, environment))
        if "images" in argv and "-q" in argv:
            return HostCommandResult(0, f"{self.image_id}\n")
        if argv[:3] == ["docker", "volume", "inspect"]:
            return HostCommandResult(
                0,
                json.dumps(
                    [
                        {
                            "Name": self.volume,
                            "Labels": {
                                "mediavault.disposable": self.disposable_label,
                                "mediavault.nonce": "test-disposable-0001",
                            },
                        }
                    ]
                ),
            )
        if argv[:2] == ["git", "-C"] and argv[-2:] == ["rev-parse", "HEAD"]:
            return HostCommandResult(0, f"{self.commit}\n")
        if argv[:2] == ["git", "-C"] and "diff" in argv:
            return HostCommandResult(1 if self.dirty_contract else 0, "")
        if argv[:3] == ["docker", "image", "inspect"]:
            return HostCommandResult(
                0,
                json.dumps(
                    [
                        {
                            "Config": {
                                "Env": [
                                    f"{key}={value}"
                                    for key, value in self.base_image_env.items()
                                ]
                            }
                        }
                    ]
                ),
            )
        if argv[:2] == ["docker", "create"]:
            service_label = next(
                value
                for value in argv
                if value.startswith("com.docker.compose.service=")
            )
            service = service_label.split("=", 1)[1]
            container_id = f"rollback-{service}"
            payload = self._payload(container_id, service, "created")
            runtime_env = {
                key: value
                for key, value in (environment or {}).items()
                if key
                not in {
                    "MEDIA_ROOT_HOST_PATH",
                    "OPERATOR_DISPOSABLE_DATABASE_VOLUME",
                    "OPERATOR_DISPOSABLE_NONCE",
                    "USER_LUT_ROOT_HOST_PATH",
                }
            }
            payload["Config"]["Env"] = [
                f"{key}={value}" for key, value in runtime_env.items()
            ]
            payload["Config"]["Cmd"] = list(ROLLBACK_SERVICE_COMMANDS[service])
            payload["HostConfig"]["Tmpfs"] = {}
            payload["Mounts"] = [
                {
                    "Type": "volume",
                    "Name": self.volume,
                    "Source": self.volume,
                    "Destination": "/data",
                    "RW": False,
                },
                {
                    "Type": "bind",
                    "Source": _env()["MEDIA_ROOT_HOST_PATH"],
                    "Destination": "/media_root",
                    "RW": False,
                },
                {
                    "Type": "bind",
                    "Source": _env()["USER_LUT_ROOT_HOST_PATH"],
                    "Destination": "/user_luts",
                    "RW": False,
                },
            ]
            self.containers[container_id] = payload
            return HostCommandResult(0, f"{container_id}\n")
        if "compose" in argv and "stop" in argv and self.compose_stop_failure:
            return HostCommandResult(1, "")
        if argv[:2] == ["docker", "ps"]:
            ids = list(self.containers)
            if self.unknown_container:
                ids.append("unknown-id")
            return HostCommandResult(0, "\n".join(ids) + ("\n" if ids else ""))
        if argv[:2] == ["docker", "inspect"]:
            container_id = argv[2]
            if container_id == "unknown-id":
                return HostCommandResult(
                    0, json.dumps([self._payload("unknown-id", "rogue", "exited")])
                )
            return HostCommandResult(0, json.dumps([self.containers[container_id]]))
        if "create" in argv:
            service = argv[-1]
            container_id = f"id-{service}"
            self.containers[container_id] = self._payload(
                container_id, service, "created"
            )
            return HostCommandResult(0, "")
        if "ps" in argv and "-q" in argv:
            service = argv[-1]
            return HostCommandResult(0, f"id-{service}\n")
        if argv[:2] == ["docker", "start"]:
            container_id = argv[2]
            if self.worker_start_failure:
                return HostCommandResult(1, "")
            service = self.containers[container_id]["Config"]["Labels"][
                "com.docker.compose.service"
            ]
            if service == self.fail_service and self.failure_version is not None:
                self.database_version = self.failure_version
            if service == "operator-worker-drain":
                state = {"Status": "running", "ExitCode": 0}
            else:
                if service == "startup-migrator" and service != self.fail_service:
                    self.database_version = "007_managed_preview_presets"
                if service == "operator-phase2b-migrator" and (
                    service != self.fail_service
                    or service == self.post_commit_failure_service
                ):
                    self.database_version = "008_formal_apple_log_preview"
                if service == "phase2c-migrator-apply" and (
                    service != self.fail_service
                    or service == self.post_commit_failure_service
                ):
                    self.database_version = "009_safe_delete_candidate"
                state = {
                    "Status": "exited",
                    "ExitCode": 1 if service == self.fail_service else 0,
                }
            self.containers[container_id]["State"] = state
            return HostCommandResult(0, container_id)
        if argv[:2] == ["docker", "exec"]:
            return HostCommandResult(0, "drained\n")
        if argv[:2] == ["docker", "logs"]:
            return HostCommandResult(
                0,
                json.dumps(
                    self.identity_payload
                    or {
                        "status": "identity_verified",
                        "last_committed_version": self.database_version,
                        "migration_count": 1,
                    }
                ),
            )
        if argv[:2] == ["docker", "stop"]:
            container_id = argv[-1]
            if container_id in self.containers:
                self.containers[container_id]["State"] = {
                    "Status": "exited",
                    "ExitCode": 0,
                }
            return HostCommandResult(0, "")
        if argv[:2] == ["docker", "rm"]:
            if (
                self.identity_cleanup_failure
                and "operator-migration-identity" in argv[-1]
            ):
                return HostCommandResult(1, "")
            self.containers.pop(argv[-1], None)
            return HostCommandResult(0, "")
        return HostCommandResult(0, "")

    def _payload(self, container_id, service, state):
        environment = self._service_env(service)
        database_rw = service not in {
            "detector-v2-preflight",
            "operator-migration-identity",
        }
        mounts = [
            {
                "Type": "volume",
                "Name": self.volume,
                "Source": self.volume,
                "Destination": "/data",
                "RW": database_rw,
            }
        ]
        if service == "operator-worker-drain":
            mounts.extend(
                [
                    {
                        "Type": "bind",
                        "Source": _env()["MEDIA_ROOT_HOST_PATH"],
                        "Destination": "/media_root",
                        "RW": True,
                    },
                    {
                        "Type": "bind",
                        "Source": _env()["USER_LUT_ROOT_HOST_PATH"],
                        "Destination": "/user_luts",
                        "RW": False,
                    },
                ]
            )
        if service == "detector-v2-preflight":
            mounts.append(
                {
                    "Type": "bind",
                    "Source": _env()["USER_LUT_ROOT_HOST_PATH"],
                    "Destination": "/user_luts",
                    "RW": False,
                }
            )
        return {
            "Id": container_id,
            "Image": IMAGE_ID,
            "Config": {
                "Labels": {
                    "com.docker.compose.service": service,
                    "com.docker.compose.project": "disposable-test",
                },
                "Env": [
                    f"{key}={value}"
                    for key, value in {**self.base_image_env, **environment}.items()
                ],
                "Cmd": list(FIXED_SERVICE_COMMANDS.get(service, ())),
                "Entrypoint": None,
            },
            "State": {"Status": state, "ExitCode": 0},
            "Mounts": mounts,
            "HostConfig": {
                "ReadonlyRootfs": True,
                "NetworkMode": "none",
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "Privileged": False,
                "Devices": [],
                "PidMode": "",
                "IpcMode": "private",
                "UTSMode": "",
                "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
                "Tmpfs": {
                    "/tmp": (
                        "rw,noexec,nosuid,nodev,size=32m"
                        if service == "operator-worker-drain"
                        else "rw,noexec,nosuid,nodev,size=16m"
                    )
                },
            },
        }

    def _service_env(self, service):
        target = {
            "OPERATOR_DATABASE_VOLUME_NAME": _env()[
                "OPERATOR_DISPOSABLE_DATABASE_VOLUME"
            ],
            "OPERATOR_DISPOSABLE_NONCE": _env()["OPERATOR_DISPOSABLE_NONCE"],
        }
        if service in {
            "operator-disposable-marker",
            "operator-migration-identity",
            "startup-migrator",
        }:
            return {"DATABASE_PATH": _env()["DATABASE_PATH"], **target}
        if service == "operator-worker-drain":
            return {
                key: _env()[key]
                for key in (
                    "API_TOKEN",
                    "DATABASE_PATH",
                    "MEDIA_ROOT",
                    "USER_LUT_ROOT",
                    "SQLITE_BUSY_TIMEOUT_MS",
                    "JOB_LEASE_SECONDS",
                )
            } | target
        if service == "detector-v2-preflight":
            return {
                "API_TOKEN": "migration-not-exposed",
                "DATABASE_PATH": "/data/mediavault.sqlite3",
                "MEDIA_ROOT": "/unmounted-media",
                "USER_LUT_ROOT": "/user_luts",
                **target,
            }
        return {
            "API_TOKEN": "migration-not-exposed",
            "DATABASE_PATH": "/data/mediavault.sqlite3",
            "MEDIA_ROOT": "/unmounted-media",
            "USER_LUT_ROOT": "",
            **target,
        }


def test_orchestration_uses_create_inspect_start_and_leaves_services_stopped(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()

    result = run_operator_release_orchestration(
        repository_root=tmp_path,
        manifest_path=manifest,
        command_runner=docker,
    )

    assert result.status == "migration_009_applied_preflight_verified_services_stopped"
    assert result.completed_phases == (
        "disposable-target-certified",
        "002-007",
        "008",
        "008-worker-drain",
        "009-dry-run",
        "009-apply",
        "010-read-only-preflight",
    )
    assert docker.containers == {}
    create_calls = [
        argv for argv, _timeout, _env_values in docker.calls if "create" in argv
    ]
    assert len(create_calls) == 7
    assert all("--no-build" in argv for argv in create_calls)
    assert all(argv[argv.index("--pull") + 1] == "never" for argv in create_calls)
    assert not any("up" in argv for argv, _timeout, _env_values in docker.calls)


def test_manifest_is_single_claim_and_never_automatically_resumes(tmp_path):
    manifest = _manifest(tmp_path)
    first = FakeDocker()
    run_operator_release_orchestration(
        repository_root=tmp_path,
        manifest_path=manifest,
        command_runner=first,
    )
    second = FakeDocker()

    with pytest.raises(
        OperatorReleaseOrchestrationError,
        match="operator_migration_operation_already_claimed",
    ):
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=second,
        )

    assert not any("stop" in argv for argv, _timeout, _env_values in second.calls)


def test_failure_keeps_services_stopped_and_does_not_continue(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    docker.fail_service = "phase2c-migrator-dry-run"

    with pytest.raises(
        OperatorReleaseOrchestrationError,
        match="operator_migration_command_failed",
    ) as captured:
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )

    assert captured.value.restore_required is True
    assert captured.value.services_stopped is True
    assert captured.value.last_committed_version == "008_formal_apple_log_preview"
    assert docker.containers == {}
    assert not any(
        argv[-1] == "phase2c-migrator-apply"
        for argv, _timeout, _env_values in docker.calls
        if "create" in argv
    )
    assert not any("up" in argv for argv, _timeout, _env_values in docker.calls)


def test_post_commit_failure_reports_read_only_verified_actual_marker(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    docker.fail_service = "operator-phase2b-migrator"
    docker.post_commit_failure_service = "operator-phase2b-migrator"

    with pytest.raises(OperatorReleaseOrchestrationError) as captured:
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )

    assert captured.value.restore_required is True
    assert captured.value.services_stopped is True
    assert captured.value.last_committed_version == "008_formal_apple_log_preview"


def test_partial_startup_failure_reports_actual_partial_marker(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    docker.fail_service = "startup-migrator"
    docker.failure_version = "004_processed_video_delivery"

    with pytest.raises(OperatorReleaseOrchestrationError) as captured:
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )

    assert captured.value.last_committed_version == "004_processed_video_delivery"
    assert captured.value.restore_required is True


def test_failure_with_invalid_identity_output_keeps_restore_required_and_unknown_marker(
    tmp_path,
):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    docker.fail_service = "startup-migrator"
    docker.identity_payload = {
        "status": "identity_verified",
        "last_committed_version": "004_processed_video_delivery",
        "migration_count": True,
    }

    with pytest.raises(OperatorReleaseOrchestrationError) as captured:
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )

    assert captured.value.last_committed_version is None
    assert captured.value.restore_required is True
    assert captured.value.services_stopped is True


def test_identity_cleanup_failure_cannot_report_services_stopped(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    docker.fail_service = "startup-migrator"
    docker.identity_cleanup_failure = True

    with pytest.raises(
        OperatorReleaseOrchestrationError,
        match="operator_migration_unsafe_stop_unconfirmed",
    ) as captured:
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )

    assert captured.value.last_committed_version is None
    assert captured.value.restore_required is True
    assert captured.value.services_stopped is False


def test_dirty_tracked_operator_contract_is_rejected_before_stop(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    docker.dirty_contract = True

    with pytest.raises(
        OperatorReleaseOrchestrationError,
        match="operator_migration_commit_mismatch",
    ):
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )

    assert not any("stop" in argv for argv, _timeout, _env in docker.calls)


@pytest.mark.parametrize(
    "service",
    [
        "startup-migrator",
        "operator-phase2b-migrator",
        "phase2c-migrator-dry-run",
        "phase2c-migrator-apply",
        "detector-v2-preflight",
    ],
)
def test_each_one_shot_failure_stops_without_starting_later_phase(tmp_path, service):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    docker.fail_service = service

    with pytest.raises(OperatorReleaseOrchestrationError):
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )

    assert docker.containers == {}
    assert not any("up" in argv for argv, _timeout, _env_values in docker.calls)


def test_worker_start_failure_is_cleaned_up_and_stays_stopped(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    docker.worker_start_failure = True

    with pytest.raises(
        OperatorReleaseOrchestrationError,
        match="operator_migration_command_failed",
    ):
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )

    assert docker.containers == {}


def test_stop_failure_reports_unsafe_stop_unconfirmed(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    docker.compose_stop_failure = True
    docker.containers["api-id"] = docker._payload("api-id", "api", "running")

    with pytest.raises(
        OperatorReleaseOrchestrationError,
        match="operator_migration_unsafe_stop_unconfirmed",
    ):
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )


def test_interrupt_cleans_created_container_and_keeps_services_stopped(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()

    def interrupting_runner(argv, timeout, environment=None):
        if (
            argv[:2] == ["docker", "start"]
            and argv[-1] == "id-operator-phase2b-migrator"
        ):
            raise KeyboardInterrupt
        return docker(argv, timeout, environment)

    with pytest.raises(
        OperatorReleaseOrchestrationError,
        match="operator_migration_interrupted",
    ):
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=interrupting_runner,
        )

    assert docker.containers == {}


def test_unknown_volume_container_is_rejected_even_when_stopped(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    docker.unknown_container = True

    with pytest.raises(
        OperatorReleaseOrchestrationError,
        match="operator_migration_unsafe_stop_unconfirmed",
    ):
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )

    assert not any("create" in argv for argv, _timeout, _env_values in docker.calls)


@pytest.mark.parametrize("state", ["paused", "restarting", "removing", "dead"])
def test_non_definitive_container_state_is_never_treated_as_stopped(tmp_path, state):
    manifest = load_manifest(_manifest(tmp_path))
    docker = FakeDocker()
    docker.containers["api-id"] = docker._payload("api-id", "api", state)

    with pytest.raises(
        OperatorReleaseOrchestrationError,
        match="operator_migration_unsafe_stop_unconfirmed",
    ):
        _require_volume_state(docker, manifest, allowed_running={})


def test_allowed_running_container_must_match_exact_container_id(tmp_path):
    manifest = load_manifest(_manifest(tmp_path))
    docker = FakeDocker()
    service = "operator-worker-drain"
    docker.containers["actual-id"] = docker._payload("actual-id", service, "running")

    with pytest.raises(
        OperatorReleaseOrchestrationError,
        match="operator_migration_unsafe_stop_unconfirmed",
    ):
        _require_volume_state(
            docker, manifest, allowed_running={service: "expected-id"}
        )


def test_duplicate_compose_service_consumer_is_rejected(tmp_path):
    manifest = load_manifest(_manifest(tmp_path))
    docker = FakeDocker()
    docker.containers["api-1"] = docker._payload("api-1", "api", "exited")
    docker.containers["api-2"] = docker._payload("api-2", "api", "exited")

    with pytest.raises(
        OperatorReleaseOrchestrationError,
        match="operator_migration_unexpected_volume_container",
    ):
        _require_volume_state(docker, manifest, allowed_running={})


def test_image_mismatch_is_rejected_before_service_stop(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    docker.image_id = f"sha256:{'b' * 64}"

    with pytest.raises(Exception, match="operator_migration_artifact_mismatch"):
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )

    assert not any("stop" in argv for argv, _timeout, _env_values in docker.calls)


@pytest.mark.parametrize("mismatch", ["volume-label", "commit"])
def test_disposable_volume_identity_and_commit_are_checked_before_stop(
    tmp_path, mismatch
):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    if mismatch == "volume-label":
        docker.disposable_label = "false"
        expected = "operator_migration_disposable_volume_invalid"
    else:
        docker.commit = "b" * 40
        expected = "operator_migration_commit_mismatch"

    with pytest.raises(OperatorReleaseOrchestrationError, match=expected):
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )

    assert not any("stop" in argv for argv, _timeout, _env_values in docker.calls)


def test_host_runner_does_not_inherit_compose_override_environment(monkeypatch):
    captured = {}
    monkeypatch.setenv("COMPOSE_FILE", "/untrusted/compose.yml")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **kwargs: (
            captured.update(kwargs)
            or subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        ),
    )

    _run_command(["docker", "compose", "version"], 30, {"API_TOKEN": "fixed"})

    assert "COMPOSE_FILE" not in captured["env"]
    assert captured["env"]["API_TOKEN"] == "fixed"


def test_rollback_reconstruction_creates_inspects_and_removes_without_start(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()

    services = validate_rollback_reconstruction(
        manifest_path=manifest,
        command_runner=docker,
    )

    assert services == ("api", "worker")
    assert docker.containers == {}
    assert (
        sum(argv[:2] == ["docker", "create"] for argv, _timeout, _env in docker.calls)
        == 2
    )
    assert not any(
        argv[:2] == ["docker", "start"] for argv, _timeout, _env in docker.calls
    )


@pytest.mark.parametrize("tamper", ["extra_env", "extra_mount", "privileged"])
def test_rollback_reconstruction_rejects_unpinned_runtime_authority(tmp_path, tamper):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()

    def tampering_runner(argv, timeout, environment=None):
        result = docker(argv, timeout, environment)
        if argv[:2] == ["docker", "create"]:
            container = docker.containers[result.stdout.strip()]
            if tamper == "extra_env":
                container["Config"]["Env"].append("EXTRA_UNPINNED=value")
            elif tamper == "extra_mount":
                container["Mounts"].append(
                    {
                        "Type": "volume",
                        "Name": "latest_template_backend-db",
                        "Source": "latest_template_backend-db",
                        "Destination": "/operator-data",
                        "RW": False,
                    }
                )
            else:
                container["HostConfig"]["Privileged"] = True
        return result

    with pytest.raises(OperatorReleaseOrchestrationError):
        validate_rollback_reconstruction(
            manifest_path=manifest,
            command_runner=tampering_runner,
        )

    assert not any(
        argv[:2] == ["docker", "start"] for argv, _timeout, _env in docker.calls
    )


def test_rollback_reconstruction_rejects_image_environment_override(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    docker.base_image_env = {"PATH": "/pinned-image-path"}

    def tampering_runner(argv, timeout, environment=None):
        result = docker(argv, timeout, environment)
        if argv[:2] == ["docker", "create"]:
            container = docker.containers[result.stdout.strip()]
            container["Config"]["Env"] = [
                "PATH=/overridden" if item.startswith("PATH=") else item
                for item in container["Config"]["Env"]
            ]
        return result

    with pytest.raises(
        OperatorReleaseOrchestrationError,
        match="operator_migration_environment_mismatch",
    ):
        validate_rollback_reconstruction(
            manifest_path=manifest,
            command_runner=tampering_runner,
        )

    assert not any(
        argv[:2] == ["docker", "start"] for argv, _timeout, _env in docker.calls
    )


@pytest.mark.parametrize("tamper", ["command", "project"])
def test_created_container_command_and_project_are_verified_before_start(
    tmp_path, tamper
):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    original_payload = docker._payload

    def tampered_payload(container_id, service, state):
        payload = original_payload(container_id, service, state)
        if service == "startup-migrator":
            if tamper == "command":
                payload["Config"]["Cmd"] = ["python", "-m", "app.main"]
            else:
                payload["Config"]["Labels"]["com.docker.compose.project"] = "other"
        return payload

    docker._payload = tampered_payload

    with pytest.raises(
        OperatorReleaseOrchestrationError,
        match="operator_migration_container_invalid",
    ):
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )

    assert not any(
        argv[:2] == ["docker", "start"] and argv[-1] == "id-startup-migrator"
        for argv, _timeout, _environment in docker.calls
    )


@pytest.mark.parametrize("tamper", ["extra_env", "extra_mount", "privileged"])
def test_created_container_rejects_unmanifested_runtime_authority(tmp_path, tamper):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    original_payload = docker._payload

    def tampered_payload(container_id, service, state):
        payload = original_payload(container_id, service, state)
        if service == "operator-disposable-marker":
            if tamper == "extra_env":
                payload["Config"]["Env"].append("EXTRA_UNPINNED=value")
            elif tamper == "extra_mount":
                payload["Mounts"].append(
                    {
                        "Type": "volume",
                        "Name": "latest_template_backend-db",
                        "Source": "latest_template_backend-db",
                        "Destination": "/operator-data",
                        "RW": True,
                    }
                )
            else:
                payload["HostConfig"]["Privileged"] = True
        return payload

    docker._payload = tampered_payload

    with pytest.raises(OperatorReleaseOrchestrationError):
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )

    assert not any(
        argv[:2] == ["docker", "start"] for argv, _timeout, _environment in docker.calls
    )


def test_created_container_rejects_image_environment_override(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    docker.base_image_env = {"PATH": "/pinned-image-path"}
    original_payload = docker._payload

    def tampered_payload(container_id, service, state):
        payload = original_payload(container_id, service, state)
        if service == "operator-disposable-marker":
            payload["Config"]["Env"] = [
                "PATH=/overridden" if item.startswith("PATH=") else item
                for item in payload["Config"]["Env"]
            ]
        return payload

    docker._payload = tampered_payload

    with pytest.raises(
        OperatorReleaseOrchestrationError,
        match="operator_migration_environment_mismatch",
    ):
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=docker,
        )

    assert not any(
        argv[:2] == ["docker", "start"] for argv, _timeout, _environment in docker.calls
    )


def test_unknown_consumer_created_during_compose_create_blocks_start(tmp_path):
    manifest = _manifest(tmp_path)
    docker = FakeDocker()
    injected = False

    def racing_runner(argv, timeout, environment=None):
        nonlocal injected
        if (
            argv[:2] == ["docker", "ps"]
            and "id-operator-disposable-marker" in docker.containers
            and not injected
        ):
            docker.containers["race-id"] = docker._payload(
                "race-id", "rogue", "running"
            )
            injected = True
        return docker(argv, timeout, environment)

    with pytest.raises(OperatorReleaseOrchestrationError):
        run_operator_release_orchestration(
            repository_root=tmp_path,
            manifest_path=manifest,
            command_runner=racing_runner,
        )

    assert not any(
        argv[:2] == ["docker", "start"] for argv, _timeout, _environment in docker.calls
    )


def test_operator_volume_is_rejected_by_manifest_without_override(tmp_path):
    docker = FakeDocker(volume="latest_template_backend-db")

    with pytest.raises(
        OperatorReleaseManifestError,
        match="operator_migration_manifest_invalid",
    ):
        _manifest(tmp_path, volume="latest_template_backend-db")

    assert docker.calls == []
