from __future__ import annotations

import os
import re
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app.services.detector_manifest import DetectorValidationError


SNAPSHOT_NAMESPACE_PATTERN = re.compile(
    r"^mediavault-detector-fixtures-[0-9a-f]{32}$"
)
MAX_TEMP_DIRECTORY_ENTRIES = 4_096
MAX_MATCHING_NAMESPACES = 64
MAX_NAMESPACE_DEPTH = 8
MAX_NAMESPACE_ENTRIES = 64
STALE_AGE_SECONDS = 300
DIRECTORY_MODE = 0o700
FILE_MODES = frozenset({0o400, 0o600})


@dataclass(frozen=True)
class EntryIdentity:
    device: int
    inode: int
    mode: int
    uid: int


def sweep_stale_snapshot_namespaces(
    *, temp_root: Path | None = None, now_ns: int | None = None
) -> int:
    root = temp_root if temp_root is not None else Path(tempfile.gettempdir())
    if not root.is_absolute():
        raise DetectorValidationError()
    current_time = time.time_ns() if now_ns is None else now_ns
    if type(current_time) is not int or current_time < 0:
        raise DetectorValidationError()
    parent_descriptor = _open_directory(root)
    removed = 0
    try:
        entries = []
        with os.scandir(parent_descriptor) as scan:
            for count, entry in enumerate(scan, start=1):
                if count > MAX_TEMP_DIRECTORY_ENTRIES:
                    raise DetectorValidationError()
                if SNAPSHOT_NAMESPACE_PATTERN.fullmatch(entry.name) is not None:
                    entries.append(entry.name)
                    if len(entries) > MAX_MATCHING_NAMESPACES:
                        raise DetectorValidationError()
        validated: list[
            tuple[str, os.stat_result, dict[tuple[str, ...], EntryIdentity]]
        ] = []
        for name in entries:
            namespace_descriptor, root_metadata = _open_child_directory(
                parent_descriptor, name
            )
            try:
                expected = _validate_namespace_tree(
                    namespace_descriptor,
                    root_metadata=root_metadata,
                )
            finally:
                os.close(namespace_descriptor)
            age_ns = current_time - root_metadata.st_mtime_ns
            if age_ns < 0:
                raise DetectorValidationError()
            validated.append((name, root_metadata, expected))
        for name, root_metadata, expected in validated:
            age_ns = current_time - root_metadata.st_mtime_ns
            if age_ns < STALE_AGE_SECONDS * 1_000_000_000:
                continue
            _remove_validated_namespace(
                parent_descriptor,
                name,
                root_metadata=root_metadata,
                expected=expected,
            )
            removed += 1
    finally:
        os.close(parent_descriptor)
    return removed


def remove_snapshot_namespace(snapshot_root: Path) -> None:
    if (
        not snapshot_root.is_absolute()
        or SNAPSHOT_NAMESPACE_PATTERN.fullmatch(snapshot_root.name) is None
    ):
        raise DetectorValidationError()
    parent_descriptor = _open_directory(snapshot_root.parent)
    try:
        try:
            namespace_descriptor, root_metadata = _open_child_directory(
                parent_descriptor, snapshot_root.name
            )
        except DetectorValidationError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                return
            raise
        try:
            expected = _validate_namespace_tree(
                namespace_descriptor,
                root_metadata=root_metadata,
            )
        finally:
            os.close(namespace_descriptor)
        _remove_validated_namespace(
            parent_descriptor,
            snapshot_root.name,
            root_metadata=root_metadata,
            expected=expected,
        )
    finally:
        os.close(parent_descriptor)


def _validate_namespace_tree(
    descriptor: int, *, root_metadata: os.stat_result
) -> dict[tuple[str, ...], EntryIdentity]:
    _require_directory_metadata(root_metadata)
    expected: dict[tuple[str, ...], EntryIdentity] = {}
    _walk_and_validate(descriptor, (), 0, expected)
    return expected


def _walk_and_validate(
    descriptor: int,
    relative: tuple[str, ...],
    depth: int,
    expected: dict[tuple[str, ...], EntryIdentity],
) -> None:
    with os.scandir(descriptor) as scan:
        names = sorted(entry.name for entry in scan)
    for name in names:
        if name in {"", ".", ".."} or "/" in name or "\x00" in name:
            raise DetectorValidationError()
        child_relative = (*relative, name)
        if len(expected) >= MAX_NAMESPACE_ENTRIES or depth + 1 > MAX_NAMESPACE_DEPTH:
            raise DetectorValidationError()
        try:
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError as exc:
            raise DetectorValidationError() from exc
        identity = _identity(metadata)
        expected[child_relative] = identity
        if stat.S_ISDIR(metadata.st_mode):
            _require_directory_metadata(metadata)
            child_descriptor, opened = _open_child_directory(descriptor, name)
            try:
                if _identity(opened) != identity:
                    raise DetectorValidationError()
                _walk_and_validate(
                    child_descriptor,
                    child_relative,
                    depth + 1,
                    expected,
                )
            finally:
                os.close(child_descriptor)
        elif stat.S_ISREG(metadata.st_mode):
            _require_file_metadata(metadata)
        else:
            raise DetectorValidationError()


def _remove_validated_namespace(
    parent_descriptor: int,
    name: str,
    *,
    root_metadata: os.stat_result,
    expected: dict[tuple[str, ...], EntryIdentity],
) -> None:
    namespace_descriptor, opened = _open_child_directory(parent_descriptor, name)
    try:
        if _identity(opened) != _identity(root_metadata):
            raise DetectorValidationError()
        _delete_children(namespace_descriptor, (), expected)
        final_root = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if _identity(final_root) != _identity(root_metadata):
            raise DetectorValidationError()
    finally:
        os.close(namespace_descriptor)
    try:
        os.rmdir(name, dir_fd=parent_descriptor)
    except OSError as exc:
        raise DetectorValidationError() from exc


def _delete_children(
    descriptor: int,
    relative: tuple[str, ...],
    expected: dict[tuple[str, ...], EntryIdentity],
) -> None:
    with os.scandir(descriptor) as scan:
        names = sorted(entry.name for entry in scan)
    expected_names = sorted(
        path[-1] for path in expected if path[:-1] == relative
    )
    if names != expected_names:
        raise DetectorValidationError()
    for name in names:
        child_relative = (*relative, name)
        recorded = expected[child_relative]
        try:
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError as exc:
            raise DetectorValidationError() from exc
        if _identity(metadata) != recorded:
            raise DetectorValidationError()
        if stat.S_ISDIR(metadata.st_mode):
            child_descriptor, opened = _open_child_directory(descriptor, name)
            try:
                if _identity(opened) != recorded:
                    raise DetectorValidationError()
                _delete_children(child_descriptor, child_relative, expected)
                final = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if _identity(final) != recorded:
                    raise DetectorValidationError()
            finally:
                os.close(child_descriptor)
            try:
                os.rmdir(name, dir_fd=descriptor)
            except OSError as exc:
                raise DetectorValidationError() from exc
        else:
            file_descriptor = None
            try:
                file_descriptor = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptor,
                )
                if _identity(os.fstat(file_descriptor)) != recorded:
                    raise DetectorValidationError()
                final = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if _identity(final) != recorded:
                    raise DetectorValidationError()
                os.unlink(name, dir_fd=descriptor)
            except OSError as exc:
                raise DetectorValidationError() from exc
            finally:
                if file_descriptor is not None:
                    os.close(file_descriptor)


def _open_directory(path: Path) -> int:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise DetectorValidationError() from exc
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise DetectorValidationError()
    return descriptor


def _open_child_directory(
    parent_descriptor: int, name: str
) -> tuple[int, os.stat_result]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise DetectorValidationError() from exc
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise DetectorValidationError()
    return descriptor, metadata


def _require_directory_metadata(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != DIRECTORY_MODE
    ):
        raise DetectorValidationError()


def _require_file_metadata(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) not in FILE_MODES
    ):
        raise DetectorValidationError()


def _identity(metadata: os.stat_result) -> EntryIdentity:
    return EntryIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        uid=metadata.st_uid,
    )
