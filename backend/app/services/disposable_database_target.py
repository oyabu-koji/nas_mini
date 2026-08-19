from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from app.services.operator_release_manifest import OPERATOR_DATABASE_VOLUME

MARKER_NAME = ".mediavault-disposable-volume.json"
MARKER_KIND = "mediavault-operator-disposable-volume-v1"
MAX_MARKER_BYTES = 4096
CONTAINER_DATABASE_PATH = Path("/data/mediavault.sqlite3")


class DisposableDatabaseTargetError(RuntimeError):
    def __init__(self, code: str = "operator_disposable_database_invalid"):
        super().__init__(code)
        self.code = code


def require_disposable_container_database(settings: object) -> None:
    """Guard legacy CLIs whenever they target the Compose database path."""
    database_path = getattr(settings, "database_path", None)
    if database_path is None or Path(database_path) != CONTAINER_DATABASE_PATH:
        return
    require_disposable_database_target(
        database_path=Path(database_path),
        volume_name=os.environ.get("OPERATOR_DATABASE_VOLUME_NAME", ""),
        nonce=os.environ.get("OPERATOR_DISPOSABLE_NONCE", ""),
    )


def initialize_disposable_database_target(
    *, database_path: Path, volume_name: str, nonce: str
) -> None:
    """Create or verify the marker after the host verified the actual volume labels."""
    _validate_target_identity(volume_name=volume_name, nonce=nonce)
    parent = database_path.parent
    if database_path.is_symlink() or parent.is_symlink() or not parent.is_dir():
        raise DisposableDatabaseTargetError()
    marker_path = parent / MARKER_NAME
    payload = json.dumps(
        {"kind": MARKER_KIND, "nonce": nonce, "volume": volume_name},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        descriptor = os.open(
            marker_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        require_disposable_database_target(
            database_path=database_path,
            volume_name=volume_name,
            nonce=nonce,
        )
        return
    except OSError as exc:
        raise DisposableDatabaseTargetError() from exc
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    require_disposable_database_target(
        database_path=database_path,
        volume_name=volume_name,
        nonce=nonce,
    )


def claim_disposable_database_operation(
    *, database_path: Path, nonce: str, operation: str
) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", operation) or not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{15,63}", nonce
    ):
        raise DisposableDatabaseTargetError()
    claim = database_path.parent / f".mediavault-{operation}.claim.json"
    payload = json.dumps(
        {
            "kind": "mediavault-operator-operation-claim-v1",
            "nonce": nonce,
            "operation": operation,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        descriptor = os.open(
            claim,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise DisposableDatabaseTargetError(
            "operator_disposable_operation_already_claimed"
        ) from exc
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def require_disposable_database_target(
    *, database_path: Path, volume_name: str, nonce: str
) -> None:
    _validate_target_identity(volume_name=volume_name, nonce=nonce)
    parent = database_path.parent
    if database_path.is_symlink() or parent.is_symlink() or not parent.is_dir():
        raise DisposableDatabaseTargetError()
    marker_path = parent / MARKER_NAME
    try:
        file_stat = marker_path.lstat()
        if (
            marker_path.is_symlink()
            or not stat.S_ISREG(file_stat.st_mode)
            or stat.S_IMODE(file_stat.st_mode) != 0o600
            or file_stat.st_uid != os.getuid()
            or file_stat.st_size > MAX_MARKER_BYTES
        ):
            raise DisposableDatabaseTargetError()
        descriptor = os.open(marker_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            payload = os.read(descriptor, MAX_MARKER_BYTES + 1)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise DisposableDatabaseTargetError() from exc
    if len(payload) > MAX_MARKER_BYTES or (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise DisposableDatabaseTargetError()
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DisposableDatabaseTargetError() from exc
    if value != {"kind": MARKER_KIND, "nonce": nonce, "volume": volume_name}:
        raise DisposableDatabaseTargetError()


def _validate_target_identity(*, volume_name: str, nonce: str) -> None:
    if (
        volume_name == OPERATOR_DATABASE_VOLUME
        or not volume_name.startswith("disposable-")
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", volume_name)
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{15,63}", nonce)
    ):
        raise DisposableDatabaseTargetError()
