from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int

    @classmethod
    def from_stat(cls, metadata: os.stat_result) -> FileIdentity:
        return cls(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            size=metadata.st_size,
            mtime_ns=metadata.st_mtime_ns,
        )


class ContainerDetectionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def linux_descriptor_path(descriptor: int) -> Path:
    _validate_descriptor_number(descriptor)
    return Path("/proc/self/fd") / str(descriptor)


def macos_descriptor_path(descriptor: int) -> Path:
    _validate_descriptor_number(descriptor)
    return Path("/dev/fd") / str(descriptor)


def resolve_descriptor_path(
    descriptor: int, *, platform_name: str | None = None
) -> Path:
    current_platform = sys.platform if platform_name is None else platform_name
    if current_platform.startswith("linux") and Path("/proc/self/fd").is_dir():
        return linux_descriptor_path(descriptor)
    if current_platform == "darwin" and Path("/dev/fd").is_dir():
        return macos_descriptor_path(descriptor)
    raise ContainerDetectionError("log_probe_failed")


def _validate_descriptor_number(descriptor: int) -> None:
    if (
        not isinstance(descriptor, int)
        or isinstance(descriptor, bool)
        or descriptor < 0
    ):
        raise ContainerDetectionError("log_probe_failed")


@dataclass
class DetectorSource:
    path: Path
    expected_size: int
    _fd: int | None = field(default=None, init=False, repr=False)
    opened_identity: FileIdentity | None = field(default=None, init=False)

    @property
    def fd(self) -> int:
        if self._fd is None:
            raise RuntimeError("detector source is not open")
        return self._fd

    def __enter__(self) -> Self:
        if self._fd is not None:
            raise RuntimeError("detector source is already open")
        try:
            path_before = os.lstat(self.path)
            self._fd = os.open(
                self.path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            metadata = os.fstat(self._fd)
            path_after = os.lstat(self.path)
            path_before_identity = FileIdentity.from_stat(path_before)
            descriptor_identity = FileIdentity.from_stat(metadata)
            path_after_identity = FileIdentity.from_stat(path_after)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or not stat.S_ISREG(path_before.st_mode)
                or not stat.S_ISREG(path_after.st_mode)
                or not isinstance(self.expected_size, int)
                or isinstance(self.expected_size, bool)
                or self.expected_size < 0
                or metadata.st_size != self.expected_size
                or path_before_identity != descriptor_identity
                or path_after_identity != descriptor_identity
            ):
                raise ContainerDetectionError("log_container_source_changed")
            self.opened_identity = descriptor_identity
        except ContainerDetectionError:
            self._close()
            raise
        except OSError as exc:
            self._close()
            raise ContainerDetectionError("log_container_source_changed") from exc
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        try:
            # Fail closed: source replacement supersedes an inner probe/parser error.
            self.verify_unchanged()
        finally:
            self._close()

    def verify_unchanged(self) -> None:
        opened_identity = self.opened_identity
        if opened_identity is None:
            raise RuntimeError("detector source has no opened identity")
        try:
            current_identity = FileIdentity.from_stat(os.fstat(self.fd))
            path_metadata = os.lstat(self.path)
            path_identity = FileIdentity.from_stat(path_metadata)
        except OSError as exc:
            raise ContainerDetectionError("log_container_source_changed") from exc
        if (
            current_identity != opened_identity
            or not stat.S_ISREG(path_metadata.st_mode)
            or path_identity != opened_identity
        ):
            raise ContainerDetectionError("log_container_source_changed")

    def read_at(self, offset: int, length: int) -> bytes:
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or not isinstance(length, int)
            or isinstance(length, bool)
            or length < 0
        ):
            raise ValueError("offset and length must be non-negative integers")
        try:
            return os.pread(self.fd, length, offset)
        except OSError as exc:
            raise ContainerDetectionError("log_container_source_changed") from exc

    def _close(self) -> None:
        descriptor = self._fd
        self._fd = None
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
