from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_image_and_ffmpeg_package_are_digest_and_version_pinned():
    dockerfile = (REPOSITORY_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

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
    assert "volumes:" not in certifier


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
