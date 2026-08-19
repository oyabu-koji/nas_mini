import json
import os

import pytest
from app.services.operator_release_manifest import (
    REQUIRED_IMAGE_SERVICES,
    OperatorReleaseManifestError,
    capture_compose_image_ids,
    load_env_source,
    load_image_id_source,
    load_manifest,
    verify_environment,
    verify_image_ids,
    write_env_source,
    write_manifest,
)
from app.services.operator_restore_drill import validate_rollback_artifacts

from scripts import prepare_operator_release_manifest as prepare_cli


def _env(token="secret"):
    return {
        "API_TOKEN": token,
        "DATABASE_PATH": "/data/mediavault.sqlite3",
        "OPERATOR_DISPOSABLE_DATABASE_VOLUME": "disposable-db-volume",
        "OPERATOR_DISPOSABLE_NONCE": "test-disposable-0001",
        "MEDIA_ROOT": "/media_root",
        "USER_LUT_ROOT": "/user_luts",
        "MEDIA_ROOT_HOST_PATH": "/private/tmp/disposable-media",
        "USER_LUT_ROOT_HOST_PATH": "/private/tmp/disposable-user-luts",
    }


def _images(seed="a"):
    return {service: f"sha256:{seed * 64}" for service in REQUIRED_IMAGE_SERVICES}


def _artifacts(tmp_path):
    release = tmp_path / "release-env.json"
    rollback = tmp_path / "rollback-env.json"
    write_env_source(release, _env("release-secret"))
    write_env_source(rollback, _env("rollback-secret"))
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        commit="a" * 40,
        compose_project="disposable-test",
        database_volume="disposable-db-volume",
        disposable_nonce="test-disposable-0001",
        database_path="/data/mediavault.sqlite3",
        release_image_ids=_images(),
        rollback_image_ids=_images("b"),
        release_env_source=release,
        rollback_env_source=rollback,
    )
    return manifest, release, rollback


def test_owner_only_manifest_and_env_round_trip(tmp_path):
    manifest_path, release_path, rollback_path = _artifacts(tmp_path)

    manifest = load_manifest(manifest_path)
    release_identity, release_values = load_env_source(release_path)
    rollback_identity, rollback_values = load_env_source(rollback_path)

    assert manifest.release_env == release_identity
    assert manifest.rollback_env == rollback_identity
    assert release_values["API_TOKEN"] == "release-secret"
    assert rollback_values["API_TOKEN"] == "rollback-secret"
    assert os.stat(manifest_path).st_mode & 0o777 == 0o600
    assert os.stat(release_path).st_mode & 0o777 == 0o600


def test_manifest_contains_digests_not_secret_values(tmp_path):
    manifest_path, _release_path, _rollback_path = _artifacts(tmp_path)
    payload = manifest_path.read_text(encoding="utf-8")

    assert "release-secret" not in payload
    assert "rollback-secret" not in payload
    assert '"sha256"' in payload
    assert '"release"' in payload
    assert '"rollback"' in payload


@pytest.mark.parametrize("artifact", ["manifest", "release", "rollback"])
def test_artifacts_reject_group_readable_mode(tmp_path, artifact):
    manifest, release, rollback = _artifacts(tmp_path)
    target = {"manifest": manifest, "release": release, "rollback": rollback}[artifact]
    target.chmod(0o640)

    with pytest.raises(OperatorReleaseManifestError):
        load_manifest(manifest)


def test_manifest_rejects_env_identity_change(tmp_path):
    manifest, release, _rollback = _artifacts(tmp_path)
    release.write_text(json.dumps(_env("changed")), encoding="utf-8")
    release.chmod(0o600)

    with pytest.raises(
        OperatorReleaseManifestError,
        match="operator_migration_environment_mismatch",
    ):
        load_manifest(manifest)


def test_env_source_rejects_unknown_and_missing_keys(tmp_path):
    with pytest.raises(OperatorReleaseManifestError):
        write_env_source(tmp_path / "unknown.json", {**_env(), "EXTRA": "value"})
    with pytest.raises(OperatorReleaseManifestError):
        write_env_source(tmp_path / "missing.json", {"API_TOKEN": "value"})


def test_image_and_environment_verification_are_exact(tmp_path):
    _manifest, release, _rollback = _artifacts(tmp_path)
    identity, values = load_env_source(release)
    verify_environment(identity, values)
    verify_image_ids(_images(), _images())

    with pytest.raises(OperatorReleaseManifestError, match="artifact_mismatch"):
        verify_image_ids(_images(), _images("b"))
    with pytest.raises(OperatorReleaseManifestError, match="environment_mismatch"):
        verify_environment(identity, _env("different"))


def test_rollback_image_and_environment_are_dry_validated_separately(tmp_path):
    manifest, _release, _rollback = _artifacts(tmp_path)

    validate_rollback_artifacts(
        manifest_path=manifest,
        actual_image_ids=_images("b"),
    )
    with pytest.raises(OperatorReleaseManifestError, match="artifact_mismatch"):
        validate_rollback_artifacts(
            manifest_path=manifest,
            actual_image_ids=_images(),
        )


def test_capture_compose_image_ids_uses_fixed_service_queries(tmp_path):
    calls = []

    def runner(argv, timeout):
        calls.append((argv, timeout))
        return f"sha256:{'a' * 64}\n"

    images = capture_compose_image_ids(
        repository_root=tmp_path,
        compose_project="disposable-test",
        command_runner=runner,
    )

    assert set(images) == set(REQUIRED_IMAGE_SERVICES)
    assert all(call[0][-3:-1] == ["images", "-q"] for call in calls)
    assert all(call[1] == 30 for call in calls)


def test_rollback_image_source_requires_owner_only_exact_map(tmp_path):
    source = tmp_path / "rollback-images.json"
    source.write_text(json.dumps(_images("b")), encoding="utf-8")
    source.chmod(0o600)

    assert load_image_id_source(source) == _images("b")
    source.chmod(0o640)
    with pytest.raises(OperatorReleaseManifestError):
        load_image_id_source(source)


def test_strict_json_rejects_duplicate_manifest_key(tmp_path):
    manifest, _release, _rollback = _artifacts(tmp_path)
    manifest.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    manifest.chmod(0o600)

    with pytest.raises(OperatorReleaseManifestError):
        load_manifest(manifest)


def test_prepare_cli_output_is_sanitized_and_does_not_build_or_pull(
    monkeypatch,
    capsys,
    tmp_path,
):
    calls = []
    monkeypatch.setattr(
        prepare_cli,
        "capture_compose_image_ids",
        lambda **_kwargs: _images(),
    )
    rollback_images = tmp_path / "rollback-images.json"
    rollback_images.write_text(json.dumps(_images("b")), encoding="utf-8")
    rollback_images.chmod(0o600)
    monkeypatch.setattr(
        prepare_cli,
        "write_manifest",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = prepare_cli.main(
        [
            "--commit",
            "a" * 40,
            "--compose-project",
            "disposable-test",
            "--database-volume",
            "disposable-db-volume",
            "--disposable-nonce",
            "test-disposable-0001",
            "--release-env-source",
            str(tmp_path / "release.json"),
            "--rollback-env-source",
            str(tmp_path / "rollback.json"),
            "--rollback-image-ids-source",
            str(rollback_images),
            "--output",
            str(tmp_path / "manifest.json"),
        ]
    )

    assert result == 0
    assert capsys.readouterr().out == "operator_migration_manifest_ready\n"
    assert calls
