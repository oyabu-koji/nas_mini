from __future__ import annotations

import stat
from pathlib import Path, PurePosixPath

from app.core.settings import Settings
from app.services.preset_manifest import (
    PRESET_ID_PATTERN,
    PresetManifest,
    PresetSnapshot,
    PresetValidationError,
    compress_only_snapshot,
    load_manifest,
    validate_cube_file,
)


BUILT_IN_PRESET_IDS = frozenset({"identity-v1", "test-red-blue-swap-v1"})


def custom_lut_capability(settings: Settings) -> bool:
    root = settings.user_lut_root
    if root is None:
        return False
    try:
        metadata = root.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(metadata.st_mode) and not root.is_symlink()


def classify_preset(settings: Settings, preset_id: str) -> PresetSnapshot:
    if preset_id == "compress-only":
        return compress_only_snapshot()
    if PRESET_ID_PATTERN.fullmatch(preset_id) is None or len(preset_id) > 64:
        return _invalid_snapshot(preset_id)
    if preset_id in BUILT_IN_PRESET_IDS:
        return _classify_candidate(
            settings=settings,
            root=settings.built_in_preset_root,
            preset_id=preset_id,
            source_root_kind="built_in",
            expected_kind={"generated-identity", "generated-test"},
        )
    if not custom_lut_capability(settings):
        return _fallback_snapshot(preset_id, "absent")
    return _classify_candidate(
        settings=settings,
        root=settings.user_lut_root,
        preset_id=preset_id,
        source_root_kind="custom",
        expected_kind={"custom"},
    )


def list_available_presets(settings: Settings) -> list[dict[str, object]]:
    snapshots = [compress_only_snapshot()]
    for preset_id in sorted(BUILT_IN_PRESET_IDS):
        snapshot = classify_preset(settings, preset_id)
        if snapshot.registry_classification == "valid":
            snapshots.append(snapshot)

    if custom_lut_capability(settings):
        assert settings.user_lut_root is not None
        try:
            candidates = sorted(settings.user_lut_root.iterdir(), key=lambda item: item.name)
        except OSError:
            candidates = []
        seen = set(BUILT_IN_PRESET_IDS) | {"compress-only"}
        for candidate in candidates:
            preset_id = candidate.name
            if preset_id in seen or PRESET_ID_PATTERN.fullmatch(preset_id) is None:
                continue
            seen.add(preset_id)
            snapshot = classify_preset(settings, preset_id)
            if snapshot.registry_classification == "valid":
                snapshots.append(snapshot)
    return [serialize_safe_preset(snapshot) for snapshot in snapshots]


def serialize_safe_preset(snapshot: PresetSnapshot) -> dict[str, object]:
    if snapshot.registry_classification != "valid" or snapshot.applied_preset_id is None:
        raise ValueError("only available presets can be serialized")
    return {
        "preset_id": snapshot.applied_preset_id,
        "display_name": snapshot.display_name,
        "preset_kind": snapshot.preset_kind,
        "enabled": True,
        "available": True,
        "version": snapshot.version,
        "target_color_space": snapshot.target_color_space,
        "source_reference": snapshot.source_reference,
        "terms_reference": snapshot.terms_reference,
    }


def _classify_candidate(
    *,
    settings: Settings,
    root: Path | None,
    preset_id: str,
    source_root_kind: str,
    expected_kind: set[str],
) -> PresetSnapshot:
    if root is None:
        return _fallback_snapshot(preset_id, "absent")
    candidate = root / preset_id
    try:
        candidate_metadata = candidate.lstat()
    except FileNotFoundError:
        return _fallback_snapshot(preset_id, "absent")
    except OSError:
        return _invalid_snapshot(preset_id)
    if not stat.S_ISDIR(candidate_metadata.st_mode) or candidate.is_symlink():
        return _invalid_snapshot(preset_id)

    try:
        manifest_path = candidate / "manifest.json"
        _require_regular_no_symlink(manifest_path)
        manifest = load_manifest(
            manifest_path, max_bytes=settings.preset_manifest_max_bytes
        )
        if manifest.preset_id != preset_id or manifest.preset_kind not in expected_kind:
            raise PresetValidationError("registry identity does not match manifest")
        if not manifest.enabled:
            return _fallback_snapshot(preset_id, "disabled", manifest=manifest)
        lut_path = _confined_lut_path(candidate, manifest.lut_relative_path)
        validate_cube_file(
            lut_path,
            expected_sha256=manifest.lut_sha256,
            expected_grid_size=manifest.grid_size,
            max_bytes=settings.preset_lut_max_bytes,
        )
    except (OSError, PresetValidationError):
        return _invalid_snapshot(preset_id)

    return PresetSnapshot(
        requested_preset_id=preset_id,
        registry_classification="valid",
        applied_preset_id=preset_id,
        display_name=manifest.display_name,
        preset_kind=manifest.preset_kind,
        version=manifest.version,
        source_reference=manifest.source_reference,
        terms_reference=manifest.terms_reference,
        target_color_space=manifest.target_color_space,
        manifest_canonical_bytes=manifest.canonical_bytes,
        manifest_sha256=manifest.manifest_sha256,
        expected_lut_sha256=manifest.lut_sha256,
        file_format=manifest.file_format,
        grid_size=manifest.grid_size,
        source_root_kind=source_root_kind,
        source_relative_lut_path=str(PurePosixPath(preset_id) / manifest.lut_relative_path),
    )


def _confined_lut_path(candidate: Path, relative_path: str) -> Path:
    current = candidate
    parts = PurePosixPath(relative_path).parts
    for index, component in enumerate(parts):
        current = current / component
        if index == len(parts) - 1:
            _require_regular_no_symlink(current)
        else:
            _require_directory_no_symlink(current)
    resolved_candidate = candidate.resolve(strict=True)
    resolved_lut = current.resolve(strict=True)
    try:
        resolved_lut.relative_to(resolved_candidate)
    except ValueError as exc:
        raise PresetValidationError("LUT path escapes registry candidate") from exc
    return current


def _require_regular_no_symlink(path: Path) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise PresetValidationError("registry file is invalid")


def _require_directory_no_symlink(path: Path) -> None:
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise PresetValidationError("registry directory is invalid")


def _fallback_snapshot(
    requested_preset_id: str,
    classification: str,
    *,
    manifest: PresetManifest | None = None,
) -> PresetSnapshot:
    fallback = compress_only_snapshot()
    return PresetSnapshot(
        requested_preset_id=requested_preset_id,
        registry_classification=classification,
        applied_preset_id="compress-only",
        display_name=manifest.display_name if manifest else fallback.display_name,
        preset_kind=manifest.preset_kind if manifest else fallback.preset_kind,
        version=manifest.version if manifest else fallback.version,
        source_reference=manifest.source_reference if manifest else fallback.source_reference,
        terms_reference=manifest.terms_reference if manifest else fallback.terms_reference,
        target_color_space=manifest.target_color_space if manifest else None,
        manifest_canonical_bytes=manifest.canonical_bytes if manifest else None,
        manifest_sha256=manifest.manifest_sha256 if manifest else None,
        expected_lut_sha256=None,
        file_format=None,
        grid_size=None,
        source_root_kind=None,
        source_relative_lut_path=None,
    )


def _invalid_snapshot(requested_preset_id: str) -> PresetSnapshot:
    return PresetSnapshot(
        requested_preset_id=requested_preset_id,
        registry_classification="registered_invalid",
        applied_preset_id=None,
        display_name=None,
        preset_kind=None,
        version=None,
        source_reference=None,
        terms_reference=None,
        target_color_space=None,
        manifest_canonical_bytes=None,
        manifest_sha256=None,
        expected_lut_sha256=None,
        file_format=None,
        grid_size=None,
        source_root_kind=None,
        source_relative_lut_path=None,
    )
