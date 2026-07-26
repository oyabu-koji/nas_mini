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


def test_phase2b_migrator_is_one_shot_offline_with_only_database_volume():
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    migrator = compose.split("  phase2b-migrator:", maxsplit=1)[1].split(
        "\nvolumes:", maxsplit=1
    )[0]

    assert "phase2b-migration" in migrator
    assert "restart:" not in migrator
    assert "ports:" not in migrator
    assert "depends_on:" not in migrator
    assert "network_mode: none" in migrator
    assert "backend-db:/data" in migrator
    assert "/media_root" not in migrator
    assert migrator.count("    volumes:") == 1
