import hashlib
import json
from dataclasses import replace

import pytest

from app.core.settings import Settings
from app.services.preset_manifest import manifest_document_with_digest
from app.services.preset_registry import (
    BUILT_IN_PRESET_IDS,
    RESERVED_PROFILE_PRESET_PAIRS,
    classify_reserved_preset_with_identity,
    classify_preset,
    custom_lut_capability,
    list_available_presets,
    serialize_safe_preset,
    reserved_profile_preset_mapping,
)
from scripts.generate_test_luts import generate_cube_bytes


def settings_for(tmp_path, **overrides):
    settings = Settings(
        media_root=tmp_path / "media",
        api_token="secret-token",
        database_path=tmp_path / "db.sqlite3",
    )
    return replace(settings, **overrides)


def write_custom(root, preset_id="custom-look", *, enabled=True, mutate=None):
    candidate = root / preset_id
    candidate.mkdir(parents=True)
    cube = generate_cube_bytes(preset_id=preset_id, transform="identity")
    (candidate / f"{preset_id}.cube").write_bytes(cube)
    values = {
        "schema_version": 1,
        "preset_id": preset_id,
        "display_name": "Custom look",
        "enabled": enabled,
        "preset_kind": "custom",
        "version": "2026-07",
        "source_reference": "Internal color team",
        "terms_reference": "Internal use record",
        "target_color_space": "Declared target",
        "lut_relative_path": f"{preset_id}.cube",
        "lut_sha256": hashlib.sha256(cube).hexdigest(),
        "file_format": "cube",
        "grid_size": 17,
    }
    if mutate:
        mutate(values)
    (candidate / "manifest.json").write_bytes(manifest_document_with_digest(values))
    return candidate


def test_custom_capability_requires_existing_non_symlink_directory(tmp_path):
    root = tmp_path / "luts"
    settings = settings_for(tmp_path, user_lut_root=root)

    assert custom_lut_capability(settings) is False
    root.mkdir()
    assert custom_lut_capability(settings) is True
    link = tmp_path / "linked-luts"
    link.symlink_to(root, target_is_directory=True)
    assert custom_lut_capability(settings_for(tmp_path, user_lut_root=link)) is False


def test_registry_classifies_absent_disabled_invalid_and_valid(tmp_path):
    root = tmp_path / "luts"
    root.mkdir()
    settings = settings_for(tmp_path, user_lut_root=root)

    assert classify_preset(settings, "missing-look").registry_classification == "absent"
    write_custom(root, "disabled-look", enabled=False)
    assert classify_preset(settings, "disabled-look").registry_classification == "disabled"
    invalid = write_custom(root, "invalid-look")
    (invalid / "invalid-look.cube").write_text("broken", encoding="utf-8")
    assert classify_preset(settings, "invalid-look").registry_classification == "registered_invalid"
    write_custom(root, "valid-look")
    assert classify_preset(settings, "valid-look").registry_classification == "valid"


def test_registry_applies_configured_manifest_and_lut_limits(tmp_path):
    root = tmp_path / "luts"
    root.mkdir()
    candidate = write_custom(root, "bounded-look")
    manifest_size = (candidate / "manifest.json").stat().st_size
    lut_size = (candidate / "bounded-look.cube").stat().st_size

    manifest_limited = settings_for(
        tmp_path,
        user_lut_root=root,
        preset_manifest_max_bytes=manifest_size - 1,
    )
    lut_limited = settings_for(
        tmp_path,
        user_lut_root=root,
        preset_lut_max_bytes=lut_size - 1,
    )

    assert classify_preset(
        manifest_limited, "bounded-look"
    ).registry_classification == "registered_invalid"
    assert classify_preset(
        lut_limited, "bounded-look"
    ).registry_classification == "registered_invalid"


def test_registry_rejects_manifest_id_mismatch_symlink_and_builtin_collision(tmp_path):
    root = tmp_path / "luts"
    root.mkdir()
    mismatch = write_custom(root, "directory-id", mutate=lambda value: value.update(preset_id="other-id"))
    assert classify_preset(settings_for(tmp_path, user_lut_root=root), "directory-id").registry_classification == "registered_invalid"

    real_manifest = mismatch / "manifest.json"
    linked = root / "linked-look"
    linked.mkdir()
    (linked / "manifest.json").symlink_to(real_manifest)
    assert classify_preset(settings_for(tmp_path, user_lut_root=root), "linked-look").registry_classification == "registered_invalid"

    write_custom(root, "identity-v1")
    ids = [item["preset_id"] for item in list_available_presets(settings_for(tmp_path, user_lut_root=root))]
    assert ids.count("identity-v1") == 1


def test_catalog_returns_only_safe_available_metadata(tmp_path):
    root = tmp_path / "luts"
    root.mkdir()
    write_custom(root, "valid-look")
    write_custom(root, "disabled-look", enabled=False)
    items = list_available_presets(settings_for(tmp_path, user_lut_root=root))

    assert items[0]["preset_id"] == "compress-only"
    assert "valid-look" in {item["preset_id"] for item in items}
    assert "disabled-look" not in {item["preset_id"] for item in items}
    serialized = json.dumps(items)
    for forbidden in ("lut_sha256", "manifest_sha256", "lut_relative_path", str(root)):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "preset_id",
    ["generated-apple-log-rec709", "generated-apple-log2-rec709"],
)
def test_reserved_automatic_preset_is_never_selectable_or_built_in(
    tmp_path,
    preset_id,
):
    root = tmp_path / "luts"
    root.mkdir()
    write_custom(root, preset_id, enabled=True)
    settings = settings_for(tmp_path, user_lut_root=root)

    catalog_ids = {
        item["preset_id"] for item in list_available_presets(settings)
    }

    assert preset_id not in BUILT_IN_PRESET_IDS
    assert preset_id not in catalog_ids
    assert not settings.built_in_preset_root.joinpath(preset_id).exists()


def test_safe_serializer_rejects_non_available_snapshot(tmp_path):
    snapshot = classify_preset(settings_for(tmp_path), "missing-look")

    with pytest.raises(ValueError):
        serialize_safe_preset(snapshot)


def test_reserved_profile_mapping_is_unique_and_closed():
    assert RESERVED_PROFILE_PRESET_PAIRS == (
        ("apple-log-1", "generated-apple-log-rec709"),
        ("apple-log-2", "generated-apple-log2-rec709"),
    )
    assert reserved_profile_preset_mapping() == dict(
        RESERVED_PROFILE_PRESET_PAIRS
    )


@pytest.mark.parametrize(
    "preset_id",
    ["generated-apple-log-rec709", "generated-apple-log2-rec709"],
)
def test_reserved_preset_identity_classifies_absent_and_disabled(
    tmp_path, preset_id
):
    built_in = tmp_path / "built-in"
    user = tmp_path / "user"
    built_in.mkdir()
    user.mkdir()
    settings = settings_for(
        tmp_path,
        built_in_preset_root=built_in,
        user_lut_root=user,
    )

    absent = classify_reserved_preset_with_identity(settings, preset_id)
    candidate = write_custom(user, preset_id, enabled=False)
    disabled = classify_reserved_preset_with_identity(settings, preset_id)

    assert absent.classification == "absent"
    assert absent.user_root is not None
    assert absent.user_candidate is None
    assert disabled.classification == "disabled"
    assert disabled.user_candidate is not None
    assert disabled.manifest is not None
    assert disabled.manifest_sha256 is not None
    assert disabled.lut is None
    assert candidate.is_dir()


@pytest.mark.parametrize(
    "preset_id",
    ["generated-apple-log-rec709", "generated-apple-log2-rec709"],
)
@pytest.mark.parametrize("with_user_candidate", [False, True])
def test_reserved_built_in_candidate_is_always_namespace_collision(
    tmp_path,
    preset_id,
    with_user_candidate,
):
    built_in = tmp_path / "built-in"
    user = tmp_path / "user"
    built_in.mkdir()
    user.mkdir()
    (built_in / preset_id).mkdir()
    if with_user_candidate:
        write_custom(user, preset_id, enabled=False)
    settings = settings_for(
        tmp_path,
        built_in_preset_root=built_in,
        user_lut_root=user,
    )

    identity = classify_reserved_preset_with_identity(
        settings,
        preset_id,
    )

    assert identity.classification == "reserved_namespace_collision"
    assert identity.built_in_candidate is not None


@pytest.mark.parametrize(
    "preset_id",
    ["generated-apple-log-rec709", "generated-apple-log2-rec709"],
)
def test_reserved_disabled_user_candidate_is_not_a_namespace_collision(
    tmp_path,
    preset_id,
):
    built_in = tmp_path / "built-in"
    user = tmp_path / "user"
    built_in.mkdir()
    user.mkdir()
    write_custom(user, preset_id, enabled=False)
    settings = settings_for(
        tmp_path,
        built_in_preset_root=built_in,
        user_lut_root=user,
    )

    identity = classify_reserved_preset_with_identity(settings, preset_id)

    assert identity.classification == "disabled"
    assert identity.built_in_candidate is None
    assert identity.user_candidate is not None


def test_reserved_selectable_catalog_overlap_is_a_namespace_collision(
    tmp_path,
    monkeypatch,
):
    built_in = tmp_path / "built-in"
    user = tmp_path / "user"
    built_in.mkdir()
    user.mkdir()
    preset_id = "generated-apple-log-rec709"
    monkeypatch.setattr(
        "app.services.preset_registry.BUILT_IN_PRESET_IDS",
        frozenset({preset_id}),
    )
    settings = settings_for(
        tmp_path,
        built_in_preset_root=built_in,
        user_lut_root=user,
    )

    identity = classify_reserved_preset_with_identity(settings, preset_id)

    assert identity.classification == "reserved_namespace_collision"


@pytest.mark.parametrize(
    "pairs",
    [
        (
            ("apple-log-1", "shared-reserved-id"),
            ("apple-log-2", "shared-reserved-id"),
        ),
        (
            ("apple-log-1", "first-reserved-id"),
            ("apple-log-1", "second-reserved-id"),
        ),
    ],
)
def test_reserved_profile_mapping_collision_is_rejected(monkeypatch, pairs):
    monkeypatch.setattr(
        "app.services.preset_registry.RESERVED_PROFILE_PRESET_PAIRS",
        pairs,
    )

    with pytest.raises(RuntimeError, match="reserved_preset_mapping_invalid"):
        reserved_profile_preset_mapping()


def test_reserved_symlink_namespace_is_registered_invalid(tmp_path):
    built_in = tmp_path / "built-in"
    user = tmp_path / "user"
    built_in.mkdir()
    user.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (user / "generated-apple-log-rec709").symlink_to(
        target,
        target_is_directory=True,
    )
    settings = settings_for(
        tmp_path,
        built_in_preset_root=built_in,
        user_lut_root=user,
    )

    identity = classify_reserved_preset_with_identity(
        settings,
        "generated-apple-log-rec709",
    )

    assert identity.classification == "registered_invalid"
