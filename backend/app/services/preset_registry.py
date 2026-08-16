from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from app.core.settings import Settings
from app.services.preset_manifest import (
    PRESET_ID_PATTERN,
    PresetManifest,
    PresetSnapshot,
    PresetValidationError,
    compress_only_snapshot,
    load_manifest_bytes,
    load_manifest,
    validate_cube_bytes,
    validate_cube_file,
)


BUILT_IN_PRESET_IDS = frozenset({"identity-v1", "test-red-blue-swap-v1"})
RESERVED_PROFILE_PRESET_PAIRS = (
    ("apple-log-1", "generated-apple-log-rec709"),
    ("apple-log-2", "generated-apple-log2-rec709"),
)
ReservedPresetClassification = Literal[
    "absent",
    "disabled",
    "registered_invalid",
    "valid",
    "reserved_namespace_collision",
]


@dataclass(frozen=True)
class RegistryFileIdentity:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int

    @classmethod
    def from_stat(cls, metadata: os.stat_result) -> RegistryFileIdentity:
        return cls(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            mode=stat.S_IFMT(metadata.st_mode),
            size=metadata.st_size,
            mtime_ns=metadata.st_mtime_ns,
        )


@dataclass(frozen=True)
class ReservedPresetRegistryIdentity:
    preset_id: str
    classification: ReservedPresetClassification
    built_in_root: RegistryFileIdentity | None
    built_in_candidate: RegistryFileIdentity | None
    user_root: RegistryFileIdentity | None
    user_candidate: RegistryFileIdentity | None
    manifest: RegistryFileIdentity | None
    manifest_sha256: str | None
    lut: RegistryFileIdentity | None
    lut_sha256: str | None


def reserved_profile_preset_mapping() -> dict[str, str]:
    profiles = [profile for profile, _preset_id in RESERVED_PROFILE_PRESET_PAIRS]
    preset_ids = [preset_id for _profile, preset_id in RESERVED_PROFILE_PRESET_PAIRS]
    if len(set(profiles)) != len(profiles) or len(set(preset_ids)) != len(preset_ids):
        raise RuntimeError("reserved_preset_mapping_invalid")
    return dict(RESERVED_PROFILE_PRESET_PAIRS)


def classify_reserved_preset_with_identity(
    settings: Settings,
    preset_id: str,
) -> ReservedPresetRegistryIdentity:
    if preset_id not in reserved_profile_preset_mapping().values():
        raise ValueError("reserved preset ID is invalid")
    built_in = _inspect_registry_namespace(
        settings.built_in_preset_root,
        preset_id=preset_id,
        manifest_max_bytes=settings.preset_manifest_max_bytes,
        lut_max_bytes=settings.preset_lut_max_bytes,
        classify_manifest=False,
    )
    user = _inspect_registry_namespace(
        settings.user_lut_root,
        preset_id=preset_id,
        manifest_max_bytes=settings.preset_manifest_max_bytes,
        lut_max_bytes=settings.preset_lut_max_bytes,
        classify_manifest=True,
    )
    if preset_id in BUILT_IN_PRESET_IDS or built_in.candidate is not None:
        classification: ReservedPresetClassification = (
            "reserved_namespace_collision"
        )
    elif built_in.invalid or user.invalid:
        classification = "registered_invalid"
    elif user.candidate is None:
        classification = "absent"
    else:
        classification = user.classification
    return ReservedPresetRegistryIdentity(
        preset_id=preset_id,
        classification=classification,
        built_in_root=built_in.root,
        built_in_candidate=built_in.candidate,
        user_root=user.root,
        user_candidate=user.candidate,
        manifest=user.manifest,
        manifest_sha256=user.manifest_sha256,
        lut=user.lut,
        lut_sha256=user.lut_sha256,
    )


@dataclass(frozen=True)
class _NamespaceInspection:
    root: RegistryFileIdentity | None
    candidate: RegistryFileIdentity | None
    manifest: RegistryFileIdentity | None = None
    manifest_sha256: str | None = None
    lut: RegistryFileIdentity | None = None
    lut_sha256: str | None = None
    classification: ReservedPresetClassification = "absent"
    invalid: bool = False


def _inspect_registry_namespace(
    root: Path | None,
    *,
    preset_id: str,
    manifest_max_bytes: int,
    lut_max_bytes: int,
    classify_manifest: bool,
) -> _NamespaceInspection:
    if root is None:
        return _NamespaceInspection(root=None, candidate=None)
    root_fd: int | None = None
    candidate_fd: int | None = None
    try:
        try:
            root_fd = os.open(
                root,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            return _NamespaceInspection(root=None, candidate=None)
        root_identity = _directory_identity(root_fd)
        try:
            candidate_fd = os.open(
                preset_id,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=root_fd,
            )
        except FileNotFoundError:
            return _NamespaceInspection(root=root_identity, candidate=None)
        candidate_identity = _directory_identity(candidate_fd)
        if not classify_manifest:
            return _NamespaceInspection(
                root=root_identity,
                candidate=candidate_identity,
            )
        manifest_bytes, manifest_identity = _read_relative_regular_file(
            candidate_fd,
            ("manifest.json",),
            max_bytes=manifest_max_bytes,
        )
        manifest = load_manifest_bytes(
            manifest_bytes,
            max_bytes=manifest_max_bytes,
        )
        if manifest.preset_id != preset_id or manifest.preset_kind != "custom":
            raise PresetValidationError("reserved preset manifest is invalid")
        manifest_digest = hashlib.sha256(manifest.canonical_bytes).hexdigest()
        if not manifest.enabled:
            return _NamespaceInspection(
                root=root_identity,
                candidate=candidate_identity,
                manifest=manifest_identity,
                manifest_sha256=manifest_digest,
                classification="disabled",
            )
        lut_bytes, lut_identity = _read_relative_regular_file(
            candidate_fd,
            PurePosixPath(manifest.lut_relative_path).parts,
            max_bytes=lut_max_bytes,
        )
        cube = validate_cube_bytes(
            lut_bytes,
            expected_sha256=manifest.lut_sha256,
            expected_grid_size=manifest.grid_size,
            max_bytes=lut_max_bytes,
        )
        return _NamespaceInspection(
            root=root_identity,
            candidate=candidate_identity,
            manifest=manifest_identity,
            manifest_sha256=manifest_digest,
            lut=lut_identity,
            lut_sha256=cube.sha256,
            classification="valid",
        )
    except (OSError, PresetValidationError):
        return _NamespaceInspection(
            root=None,
            candidate=None,
            classification="registered_invalid",
            invalid=True,
        )
    finally:
        if candidate_fd is not None:
            os.close(candidate_fd)
        if root_fd is not None:
            os.close(root_fd)


def _directory_identity(descriptor: int) -> RegistryFileIdentity:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise PresetValidationError("registry directory is invalid")
    return RegistryFileIdentity.from_stat(metadata)


def _read_relative_regular_file(
    parent_fd: int,
    parts: tuple[str, ...],
    *,
    max_bytes: int,
) -> tuple[bytes, RegistryFileIdentity]:
    directory_fds: list[int] = []
    current_fd = parent_fd
    file_fd: int | None = None
    try:
        for component in parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current_fd,
            )
            _directory_identity(next_fd)
            directory_fds.append(next_fd)
            current_fd = next_fd
        file_fd = os.open(
            parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=current_fd,
        )
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= max_bytes:
            raise PresetValidationError("registry file is invalid")
        raw = bytearray()
        while len(raw) <= max_bytes:
            chunk = os.read(file_fd, min(65_536, max_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(file_fd)
        if (
            RegistryFileIdentity.from_stat(before)
            != RegistryFileIdentity.from_stat(after)
            or len(raw) > max_bytes
        ):
            raise PresetValidationError("registry file changed")
        return bytes(raw), RegistryFileIdentity.from_stat(after)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for descriptor in reversed(directory_fds):
            os.close(descriptor)


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
    reserved_ids = frozenset(reserved_profile_preset_mapping().values())
    for preset_id in sorted(BUILT_IN_PRESET_IDS):
        if preset_id in reserved_ids:
            continue
        snapshot = classify_preset(settings, preset_id)
        if snapshot.registry_classification == "valid":
            snapshots.append(snapshot)

    if custom_lut_capability(settings):
        assert settings.user_lut_root is not None
        try:
            candidates = sorted(settings.user_lut_root.iterdir(), key=lambda item: item.name)
        except OSError:
            candidates = []
        seen = set(BUILT_IN_PRESET_IDS) | set(reserved_ids) | {"compress-only"}
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
