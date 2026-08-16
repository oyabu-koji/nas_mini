import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
USER_LUT_CONTAINER_ROOT = "/user_luts"
USER_LUT_DEFAULT_HOST_ROOT = "/private/tmp/mediavault-user-luts"


def _compose_service(compose: str, service_name: str) -> str:
    service = re.search(
        rf"^  {re.escape(service_name)}:\n(.*?)(?=^  [\w-]+:|^volumes:|\Z)",
        compose,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert service is not None
    return service.group(1)


def test_local_detector_fixture_workspace_is_root_ignored():
    gitignore_lines = {
        line.strip()
        for line in (REPOSITORY_ROOT / ".gitignore")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "/data/" in gitignore_lines


def test_docker_build_contexts_cannot_include_repository_data_workspace():
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    contexts = re.findall(r"^\s+context:\s+([^\s#]+)\s*$", compose, flags=re.MULTILINE)
    repository_data = (REPOSITORY_ROOT / "data").resolve()

    assert contexts
    for configured_context in contexts:
        resolved_context = (REPOSITORY_ROOT / configured_context).resolve()
        assert not repository_data.is_relative_to(resolved_context)


def test_user_lut_consumers_share_container_root_and_read_only_mount():
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    services = {
        "api": _compose_service(compose, "api"),
        "worker": _compose_service(compose, "worker"),
        "detector-v2-migrator": _compose_service(compose, "detector-v2-migrator"),
    }
    expected_environment = f"USER_LUT_ROOT: {USER_LUT_CONTAINER_ROOT}"
    expected_mount = (
        "${USER_LUT_ROOT_HOST_PATH:-"
        f"{USER_LUT_DEFAULT_HOST_ROOT}}}:{USER_LUT_CONTAINER_ROOT}:ro"
    )

    for service in services.values():
        assert expected_environment in service
        assert expected_mount in service


def test_unset_user_lut_host_root_uses_consistent_default_bind_mount():
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    services = (
        _compose_service(compose, "api"),
        _compose_service(compose, "worker"),
        _compose_service(compose, "detector-v2-migrator"),
    )
    mount_pattern = re.compile(r"\$\{USER_LUT_ROOT_HOST_PATH:-([^}]+)\}:(/[^:]+):ro")

    for service in services:
        mount = mount_pattern.search(service)
        assert mount is not None
        assert mount.groups() == (USER_LUT_DEFAULT_HOST_ROOT, USER_LUT_CONTAINER_ROOT)


def test_runtime_image_and_ffmpeg_package_are_digest_and_version_pinned():
    dockerfile = (REPOSITORY_ROOT / "backend" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert (
        "python:3.12-slim@sha256:"
        "57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
    ) in dockerfile
    assert "FFMPEG_PACKAGE_VERSION=7:7.1.5-0+deb13u1" in dockerfile
    assert '"ffmpeg=${FFMPEG_PACKAGE_VERSION}"' in dockerfile


def test_detector_certifier_is_profiled_read_only_and_offline():
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    certifier = compose.split("  detector-certifier:", maxsplit=1)[1].split(
        "\n  phase2b-migrator:", maxsplit=1
    )[0]

    assert "detector-certification" in certifier
    assert "read_only: true" in certifier
    assert "network_mode: none" in certifier
    assert "no-new-privileges:true" in certifier
    assert "/tmp:rw,noexec,nosuid,nodev,size=16m" in certifier
    assert "volumes:" in certifier
    assert (
        "${DETECTOR_FIXTURE_ROOT:-/private/tmp/mediavault-detector-fixtures-unconfigured}:/fixtures:ro"
        in certifier
    )


def test_image_codec_validator_uses_production_image_and_is_isolated():
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    service = compose.split("  image-codec-validator:", maxsplit=1)[1].split(
        "\n  detector-certifier:", maxsplit=1
    )[0]

    assert "image-codec-validation" in service
    assert "context: ./backend" in service
    assert "scripts/validate_image_codecs.py" in service
    assert "/fixtures/valid/manifest.json" in service
    assert "UV_CACHE_DIR: /tmp/uv-cache" in service
    assert "read_only: true" in service
    assert "network_mode: none" in service
    assert "no-new-privileges:true" in service
    assert "/tmp:rw,noexec,nosuid,nodev,size=32m" in service
    assert "./backend/tests/fixtures/image-codecs:/fixtures:ro" in service
    assert "MEDIA_ROOT" not in service
    assert "DATABASE_PATH" not in service
    assert "LUT_PATH" not in service
    assert "/media_root" not in service
    assert "/user_luts" not in service


def test_phase2b_migrator_is_one_shot_offline_with_only_database_volume():
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    migrator = compose.split("  phase2b-migrator:", maxsplit=1)[1].split(
        "\n  phase2c-migrator:", maxsplit=1
    )[0]

    assert "phase2b-migration" in migrator
    assert "restart:" not in migrator
    assert "ports:" not in migrator
    assert "depends_on:" not in migrator
    assert "network_mode: none" in migrator
    assert "backend-db:/data" in migrator
    assert "/media_root" not in migrator
    assert migrator.count("    volumes:") == 1


def test_phase2c_operator_services_use_importable_module_entrypoints():
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    migrator = compose.split("  phase2c-migrator:", maxsplit=1)[1].split(
        "\n  phase2c-reconciler:", maxsplit=1
    )[0]
    reconciler = compose.split("  phase2c-reconciler:", maxsplit=1)[1].split(
        "\nvolumes:", maxsplit=1
    )[0]

    assert "scripts.migrate_phase2c_safe_delete_candidate" in migrator
    assert "scripts/migrate_phase2c_safe_delete_candidate.py" not in migrator
    assert "scripts.reconcile_safe_delete_candidates" in reconciler
    assert "scripts/reconcile_safe_delete_candidates.py" not in reconciler
