import hashlib
import json
from dataclasses import replace

import pytest

from app.core.settings import Settings
from app.services.preset_manifest import manifest_document_with_digest
from app.services.preset_registry import (
    classify_preset,
    custom_lut_capability,
    list_available_presets,
    serialize_safe_preset,
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


def test_safe_serializer_rejects_non_available_snapshot(tmp_path):
    snapshot = classify_preset(settings_for(tmp_path), "missing-look")

    with pytest.raises(ValueError):
        serialize_safe_preset(snapshot)
