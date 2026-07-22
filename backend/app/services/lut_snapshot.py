from __future__ import annotations

import hashlib
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core.settings import Settings
from app.services.preset_manifest import LUT_MAX_BYTES, SHA256_PATTERN
from app.services.storage import resolve_media_path


COPY_BLOCK_BYTES = 1024 * 1024


class LutSnapshotError(RuntimeError):
    def __init__(self, code: str = "lut_preset_source_changed"):
        super().__init__(code)
        self.code = code


@dataclass
class OpenedLutSource:
    fd: int
    size_bytes: int

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> "OpenedLutSource":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


@dataclass(frozen=True)
class LutSnapshot:
    path: Path
    size_bytes: int
    sha256: str


def open_lut_source(
    *, settings: Settings, source_root_kind: str, source_relative_path: str
) -> OpenedLutSource:
    root = _source_root(settings, source_root_kind)
    parts = _safe_components(source_relative_path)
    opened: list[int] = []
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | nofollow
    try:
        root_fd = os.open(root, directory_flags)
        opened.append(root_fd)
        current_fd = root_fd
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=current_fd)
            opened.append(next_fd)
            current_fd = next_fd
        leaf_fd = os.open(parts[-1], os.O_RDONLY | nofollow, dir_fd=current_fd)
        metadata = os.fstat(leaf_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 0 < metadata.st_size <= settings.preset_lut_max_bytes
        ):
            os.close(leaf_fd)
            raise LutSnapshotError()
        return OpenedLutSource(fd=leaf_fd, size_bytes=metadata.st_size)
    except (OSError, ValueError) as exc:
        if isinstance(exc, LutSnapshotError):
            raise
        raise LutSnapshotError() from exc
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def copy_opened_lut_to_snapshot(
    *,
    settings: Settings,
    rendition_id: str,
    source: OpenedLutSource,
    expected_sha256: str,
) -> LutSnapshot:
    if SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise LutSnapshotError()
    job_dir = _prepare_job_directory(settings, rendition_id)
    snapshot_path = job_dir / "lut.cube"
    digest = hashlib.sha256()
    copied = 0
    output_fd: int | None = None
    try:
        output_fd = os.open(snapshot_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.lseek(source.fd, 0, os.SEEK_SET)
        while True:
            block = os.read(source.fd, COPY_BLOCK_BYTES)
            if not block:
                break
            copied += len(block)
            if copied > settings.preset_lut_max_bytes:
                raise LutSnapshotError()
            digest.update(block)
            _write_all(output_fd, block)
        os.fsync(output_fd)
    except (OSError, LutSnapshotError) as exc:
        cleanup_lut_snapshot(settings=settings, rendition_id=rendition_id)
        if isinstance(exc, LutSnapshotError):
            raise
        raise LutSnapshotError() from exc
    finally:
        if output_fd is not None:
            os.close(output_fd)
    calculated = digest.hexdigest()
    if copied != source.size_bytes or calculated != expected_sha256:
        cleanup_lut_snapshot(settings=settings, rendition_id=rendition_id)
        raise LutSnapshotError()
    return LutSnapshot(snapshot_path, copied, calculated)


def create_lut_snapshot(
    *,
    settings: Settings,
    rendition_id: str,
    source_root_kind: str,
    source_relative_path: str,
    expected_sha256: str,
) -> LutSnapshot:
    with open_lut_source(
        settings=settings,
        source_root_kind=source_root_kind,
        source_relative_path=source_relative_path,
    ) as source:
        return copy_opened_lut_to_snapshot(
            settings=settings,
            rendition_id=rendition_id,
            source=source,
            expected_sha256=expected_sha256,
        )


def verify_lut_snapshot(snapshot: LutSnapshot, *, expected_sha256: str) -> None:
    try:
        metadata = snapshot.path.lstat()
        if snapshot.path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise LutSnapshotError()
        if (
            metadata.st_size != snapshot.size_bytes
            or metadata.st_size > LUT_MAX_BYTES
        ):
            raise LutSnapshotError()
        digest = hashlib.sha256()
        with snapshot.path.open("rb") as source:
            while block := source.read(COPY_BLOCK_BYTES):
                digest.update(block)
    except OSError as exc:
        raise LutSnapshotError() from exc
    if digest.hexdigest() != expected_sha256 or snapshot.sha256 != expected_sha256:
        raise LutSnapshotError()


def cleanup_lut_snapshot(*, settings: Settings, rendition_id: str) -> None:
    normalized = _rendition_id(rendition_id)
    jobs_fd: int | None = None
    try:
        jobs_fd = _open_jobs_directory(settings, create=False)
        _remove_job_entry(jobs_fd, normalized)
    except OSError:
        pass
    finally:
        if jobs_fd is not None:
            os.close(jobs_fd)


def _source_root(settings: Settings, source_root_kind: str) -> Path:
    if source_root_kind == "built_in":
        return settings.built_in_preset_root
    if source_root_kind == "custom" and settings.user_lut_root is not None:
        return settings.user_lut_root
    raise LutSnapshotError()


def _safe_components(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or "\x00" in value:
        raise LutSnapshotError()
    raw_parts = tuple(value.split("/"))
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise LutSnapshotError()
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts:
        raise LutSnapshotError()
    return path.parts


def _prepare_job_directory(settings: Settings, rendition_id: str) -> Path:
    normalized = _rendition_id(rendition_id)
    jobs_fd: int | None = None
    try:
        jobs_fd = _open_jobs_directory(settings, create=True)
        _remove_job_entry(jobs_fd, normalized)
        os.mkdir(normalized, mode=0o700, dir_fd=jobs_fd)
    except OSError as exc:
        raise LutSnapshotError() from exc
    finally:
        if jobs_fd is not None:
            os.close(jobs_fd)
    return settings.media_root.resolve() / "jobs" / normalized


def _open_jobs_directory(settings: Settings, *, create: bool) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(settings.media_root.resolve(), flags)
    try:
        if create:
            try:
                os.mkdir("jobs", mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
        return os.open("jobs", flags, dir_fd=root_fd)
    finally:
        os.close(root_fd)


def _remove_job_entry(jobs_fd: int, rendition_id: str) -> None:
    try:
        metadata = os.stat(rendition_id, dir_fd=jobs_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(metadata.st_mode):
        shutil.rmtree(rendition_id, dir_fd=jobs_fd)
    else:
        os.unlink(rendition_id, dir_fd=jobs_fd)


def _rendition_id(value: str) -> str:
    if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise LutSnapshotError()
    return value


def _write_all(fd: int, block: bytes) -> None:
    offset = 0
    while offset < len(block):
        written = os.write(fd, block[offset:])
        if written <= 0:
            raise OSError("snapshot write failed")
        offset += written
