from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote

from app.services.operator_release_manifest import (
    OPERATOR_DATABASE_VOLUME,
    OperatorReleaseManifestError,
    load_env_source,
    load_manifest,
    verify_image_ids,
)

DISPOSABLE_MARKER_NAME = ".mediavault-disposable-restore.json"
DISPOSABLE_MARKER_KIND = "mediavault-operator-restore-v1"
ATTESTATION_SUFFIX = ".fresh-backup.json"
MAX_DATABASE_BYTES = 64 * 1024 * 1024 * 1024
MAX_MEDIA_FILES = 10_000
MAX_TABLES = 256
MEDIA_TREES = ("originals", "previews", "thumbnails")
DERIVED_TREES = ("previews", "thumbnails")


class OperatorRestoreDrillError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DatabaseIdentity:
    versions: tuple[str, ...]
    schema_sha256: str
    table_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class MediaFileIdentity:
    relative_path: str
    size: int
    mode: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class RestoreDrillResult:
    status: str
    schema_version: str
    table_count: int
    protected_media_count: int
    removed_operation_orphans: int


def write_disposable_marker(root: Path, *, nonce: str) -> Path:
    """Initialize only a narrowly named empty disposable root."""
    _validate_nonce(nonce)
    root = root.resolve()
    if root.parent != Path("/private/tmp") or not root.name.startswith(
        "mediavault-operator-"
    ):
        raise OperatorRestoreDrillError("restore_disposable_root_invalid")
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    marker = root / DISPOSABLE_MARKER_NAME
    _write_owner_only_json(marker, {"kind": DISPOSABLE_MARKER_KIND, "nonce": nonce})
    return marker


def initialize_mounted_disposable_root(
    root: Path, *, nonce: str, database_volume: str
) -> Path:
    """Initialize the exact empty mountpoint used by the disposable Docker drill."""
    _validate_nonce(nonce)
    _require_non_operator_volume(database_volume)
    if root != Path("/restore") or root.is_symlink() or not root.is_dir():
        raise OperatorRestoreDrillError("restore_disposable_root_invalid")
    if any(root.iterdir()):
        raise OperatorRestoreDrillError("restore_disposable_root_not_empty")
    marker = root / DISPOSABLE_MARKER_NAME
    _write_owner_only_json(marker, {"kind": DISPOSABLE_MARKER_KIND, "nonce": nonce})
    return marker


def create_fresh_backup(
    *,
    source_database: Path,
    backup_database: Path,
    disposable_root: Path,
    nonce: str,
    database_volume: str,
) -> DatabaseIdentity:
    root = _require_disposable_root(disposable_root, nonce=nonce)
    _require_non_operator_volume(database_volume)
    backup = _require_child_path(backup_database, root)
    if backup.exists() or backup.is_symlink():
        raise OperatorRestoreDrillError("restore_backup_destination_not_empty")
    _backup_database(source_database, backup)
    os.chmod(backup, 0o600)
    identity = inspect_database(backup)
    attestation = _attestation_path(backup)
    _write_owner_only_json(
        attestation,
        {
            "kind": DISPOSABLE_MARKER_KIND,
            "nonce": nonce,
            "database_sha256": _file_sha256(backup),
            "identity": _database_identity_payload(identity),
        },
    )
    return identity


def restore_database(
    *,
    backup_database: Path,
    target_database: Path,
    disposable_root: Path,
    nonce: str,
    database_volume: str,
) -> DatabaseIdentity:
    root = _require_disposable_root(disposable_root, nonce=nonce)
    _require_non_operator_volume(database_volume)
    backup = _require_child_path(backup_database, root)
    target = _require_child_path(target_database, root)
    expected = _load_backup_attestation(backup, nonce=nonce)
    if _file_sha256(backup) != expected[0]:
        raise OperatorRestoreDrillError("restore_backup_identity_mismatch")
    if inspect_database(backup) != expected[1]:
        raise OperatorRestoreDrillError("restore_backup_identity_mismatch")
    _require_replaceable_target(target)
    _cleanup_sidecars(target)
    temporary_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=".restore-", suffix=".sqlite3", dir=root
        )
        os.close(descriptor)
        temporary_path = Path(raw_path)
        _backup_database(backup, temporary_path)
        os.chmod(temporary_path, 0o600)
        if inspect_database(temporary_path) != expected[1]:
            raise OperatorRestoreDrillError("restore_database_identity_mismatch")
        os.replace(temporary_path, target)
        temporary_path = None
        _cleanup_sidecars(target)
        actual = inspect_database(target)
        if actual != expected[1]:
            raise OperatorRestoreDrillError("restore_database_identity_mismatch")
        return actual
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def inspect_database(database_path: Path) -> DatabaseIdentity:
    _require_regular_file(database_path, "restore_database_invalid")
    size = database_path.stat().st_size
    if size <= 0 or size > MAX_DATABASE_BYTES:
        raise OperatorRestoreDrillError("restore_database_invalid")
    conn = _connect_read_only(database_path)
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise OperatorRestoreDrillError("restore_database_integrity_invalid")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise OperatorRestoreDrillError("restore_database_foreign_key_invalid")
        schema_rows = conn.execute(
            """
            SELECT type, name, tbl_name, COALESCE(sql, '') AS sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
        canonical = "\n".join(
            "\x1f".join(str(row[key]) for key in ("type", "name", "tbl_name", "sql"))
            for row in schema_rows
        )
        tables = sorted(
            str(row["name"]) for row in schema_rows if row["type"] == "table"
        )
        if len(tables) > MAX_TABLES:
            raise OperatorRestoreDrillError("restore_database_inventory_limit")
        counts = tuple((table, _bounded_count(conn, table)) for table in tables)
        versions = tuple(
            str(row[0])
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY applied_at, rowid"
            ).fetchall()
        )
        if not versions or len(versions) != len(set(versions)):
            raise OperatorRestoreDrillError("restore_database_marker_invalid")
        return DatabaseIdentity(
            versions=versions,
            schema_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            table_counts=counts,
        )
    except sqlite3.DatabaseError as exc:
        raise OperatorRestoreDrillError("restore_database_invalid") from exc
    finally:
        conn.close()


def snapshot_media(media_root: Path) -> tuple[MediaFileIdentity, ...]:
    root = _require_media_root(media_root)
    items: list[MediaFileIdentity] = []
    for tree in MEDIA_TREES:
        tree_path = root / tree
        if not tree_path.exists():
            continue
        if tree_path.is_symlink() or not tree_path.is_dir():
            raise OperatorRestoreDrillError("restore_media_inventory_invalid")
        for directory, directory_names, filenames in os.walk(
            tree_path, followlinks=False
        ):
            directory_names.sort()
            filenames.sort()
            current = Path(directory)
            for name in tuple(directory_names):
                path = current / name
                if path.is_symlink():
                    raise OperatorRestoreDrillError("restore_media_inventory_invalid")
            for name in filenames:
                path = current / name
                if len(items) >= MAX_MEDIA_FILES:
                    raise OperatorRestoreDrillError("restore_media_inventory_limit")
                file_stat = path.lstat()
                if not stat.S_ISREG(file_stat.st_mode) or path.is_symlink():
                    raise OperatorRestoreDrillError("restore_media_inventory_invalid")
                relative = path.relative_to(root).as_posix()
                items.append(
                    MediaFileIdentity(
                        relative_path=relative,
                        size=file_stat.st_size,
                        mode=stat.S_IMODE(file_stat.st_mode),
                        mtime_ns=file_stat.st_mtime_ns,
                        sha256=_file_sha256(path),
                    )
                )
    return tuple(items)


def reconcile_media_after_restore(
    *,
    database_path: Path,
    media_root: Path,
    protected_before: tuple[MediaFileIdentity, ...],
    operation_derived_paths: tuple[str, ...],
    disposable_root: Path,
    nonce: str,
) -> tuple[int, int]:
    disposable = _require_disposable_root(disposable_root, nonce=nonce)
    root = _require_media_root(media_root, disposable_root=disposable)
    protected = {item.relative_path: item for item in protected_before}
    if len(protected) != len(protected_before):
        raise OperatorRestoreDrillError("restore_media_inventory_invalid")
    candidates = tuple(dict.fromkeys(operation_derived_paths))
    if (
        len(candidates) != len(operation_derived_paths)
        or len(candidates) > MAX_MEDIA_FILES
    ):
        raise OperatorRestoreDrillError("restore_media_inventory_invalid")
    for relative in candidates:
        _require_derived_relative_path(relative)
        if relative in protected:
            raise OperatorRestoreDrillError("restore_media_protected_overlap")
    current_before_cleanup = {item.relative_path: item for item in snapshot_media(root)}
    for relative, expected in protected.items():
        if current_before_cleanup.get(relative) != expected:
            raise OperatorRestoreDrillError("restore_media_protected_changed")
    referenced = _database_derived_paths(database_path)
    removed = 0
    for relative in candidates:
        path = _resolve_media_path(root, relative)
        if relative in referenced or not path.exists():
            continue
        _require_regular_file(path, "restore_media_inventory_invalid")
        path.unlink()
        removed += 1
    current = {item.relative_path: item for item in snapshot_media(root)}
    for relative, expected in protected.items():
        if current.get(relative) != expected:
            raise OperatorRestoreDrillError("restore_media_protected_changed")
    missing = [relative for relative in referenced if relative not in current]
    derived_files = {
        relative for relative in current if relative.split("/", 1)[0] in DERIVED_TREES
    }
    remaining_orphans = derived_files - referenced
    if missing or remaining_orphans:
        raise OperatorRestoreDrillError("restore_media_reconciliation_invalid")
    return removed, len(protected)


def run_restore_drill(
    *,
    backup_database: Path,
    target_database: Path,
    disposable_root: Path,
    nonce: str,
    database_volume: str,
    media_root: Path,
    protected_before: tuple[MediaFileIdentity, ...],
    operation_derived_paths: tuple[str, ...],
) -> RestoreDrillResult:
    root = _require_disposable_root(disposable_root, nonce=nonce)
    _require_media_root(media_root, disposable_root=root)
    _record_failure_forensics(
        database_path=target_database,
        backup_database=backup_database,
        disposable_root=root,
        nonce=nonce,
    )
    identity = restore_database(
        backup_database=backup_database,
        target_database=target_database,
        disposable_root=disposable_root,
        nonce=nonce,
        database_volume=database_volume,
    )
    removed, protected_count = reconcile_media_after_restore(
        database_path=target_database,
        media_root=media_root,
        protected_before=protected_before,
        operation_derived_paths=operation_derived_paths,
        disposable_root=disposable_root,
        nonce=nonce,
    )
    return RestoreDrillResult(
        status="restore_drill_complete",
        schema_version=identity.versions[-1],
        table_count=len(identity.table_counts),
        protected_media_count=protected_count,
        removed_operation_orphans=removed,
    )


def _record_failure_forensics(
    *,
    database_path: Path,
    backup_database: Path,
    disposable_root: Path,
    nonce: str,
) -> None:
    target = _require_child_path(database_path, disposable_root)
    backup = _require_child_path(backup_database, disposable_root)
    _require_regular_file(backup, "restore_database_invalid")
    try:
        identity_payload: dict = {
            "status": "valid",
            "value": _database_identity_payload(inspect_database(target)),
        }
    except OperatorRestoreDrillError as exc:
        identity_payload = {"status": "unavailable", "reason": exc.code}
    files = []
    for path in (target, Path(f"{target}-wal"), Path(f"{target}-shm")):
        if not path.exists() and not path.is_symlink():
            files.append({"name": path.name, "present": False})
            continue
        _require_regular_file(path, "restore_database_invalid")
        file_stat = path.stat()
        files.append(
            {
                "name": path.name,
                "present": True,
                "size": file_stat.st_size,
                "mode": stat.S_IMODE(file_stat.st_mode),
                "mtime_ns": file_stat.st_mtime_ns,
                "sha256": _file_sha256(path),
            }
        )
    artifact = disposable_root / "failure-forensic.json"
    _write_owner_only_json(
        artifact,
        {
            "kind": "mediavault-operator-failure-forensic-v1",
            "nonce": nonce,
            "backup_database_sha256": _file_sha256(backup),
            "backup_attestation_sha256": _file_sha256(_attestation_path(backup)),
            "database_identity": identity_payload,
            "files": files,
        },
    )


def validate_rollback_artifacts(
    *, manifest_path: Path, actual_image_ids: dict[str, str]
) -> None:
    manifest = load_manifest(manifest_path)
    identity, _values = load_env_source(
        manifest_path.parent / manifest.rollback_env.filename
    )
    if identity != manifest.rollback_env:
        raise OperatorReleaseManifestError("operator_migration_environment_mismatch")
    verify_image_ids(manifest.rollback_image_ids, actual_image_ids)


def _database_derived_paths(database_path: Path) -> set[str]:
    conn = _connect_read_only(database_path)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='derived_files'"
        ).fetchone()
        if exists is None:
            return set()
        rows = conn.execute(
            "SELECT path FROM derived_files ORDER BY path LIMIT ?",
            (MAX_MEDIA_FILES + 1,),
        ).fetchall()
        if len(rows) > MAX_MEDIA_FILES:
            raise OperatorRestoreDrillError("restore_media_inventory_limit")
        result = set()
        for row in rows:
            relative = str(row[0])
            _require_derived_relative_path(relative)
            result.add(relative)
        if len(result) != len(rows):
            raise OperatorRestoreDrillError("restore_media_inventory_invalid")
        return result
    except sqlite3.DatabaseError as exc:
        raise OperatorRestoreDrillError("restore_database_invalid") from exc
    finally:
        conn.close()


def _backup_database(source: Path, destination: Path) -> None:
    _require_regular_file(source, "restore_database_invalid")
    source_conn = _connect_backup_source(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
        destination_conn.commit()
    except sqlite3.DatabaseError as exc:
        raise OperatorRestoreDrillError("restore_backup_failed") from exc
    finally:
        destination_conn.close()
        source_conn.close()


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro&immutable=1"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.DatabaseError as exc:
        raise OperatorRestoreDrillError("restore_database_invalid") from exc


def _connect_backup_source(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.DatabaseError as exc:
        raise OperatorRestoreDrillError("restore_database_invalid") from exc


def _load_backup_attestation(
    backup: Path, *, nonce: str
) -> tuple[str, DatabaseIdentity]:
    _require_regular_file(backup, "restore_database_invalid")
    payload = _read_owner_only_json(_attestation_path(backup))
    try:
        if set(payload) != {"kind", "nonce", "database_sha256", "identity"}:
            raise ValueError
        if payload["kind"] != DISPOSABLE_MARKER_KIND or payload["nonce"] != nonce:
            raise ValueError
        identity_payload = payload["identity"]
        if set(identity_payload) != {"versions", "schema_sha256", "table_counts"}:
            raise ValueError
        identity = DatabaseIdentity(
            versions=tuple(str(value) for value in identity_payload["versions"]),
            schema_sha256=str(identity_payload["schema_sha256"]),
            table_counts=tuple(
                (str(row[0]), int(row[1])) for row in identity_payload["table_counts"]
            ),
        )
        digest = str(payload["database_sha256"])
        if len(digest) != 64:
            raise ValueError
        return digest, identity
    except (KeyError, TypeError, ValueError) as exc:
        raise OperatorRestoreDrillError("restore_backup_attestation_invalid") from exc


def _database_identity_payload(identity: DatabaseIdentity) -> dict:
    payload = asdict(identity)
    payload["versions"] = list(identity.versions)
    payload["table_counts"] = [list(row) for row in identity.table_counts]
    return payload


def _require_disposable_root(root: Path, *, nonce: str) -> Path:
    _validate_nonce(nonce)
    resolved = root.resolve()
    host_root_valid = resolved.parent == Path(
        "/private/tmp"
    ) and resolved.name.startswith("mediavault-operator-")
    if (resolved != Path("/restore") and not host_root_valid) or root.is_symlink():
        raise OperatorRestoreDrillError("restore_disposable_root_invalid")
    if not resolved.is_dir():
        raise OperatorRestoreDrillError("restore_disposable_root_invalid")
    marker = _read_owner_only_json(resolved / DISPOSABLE_MARKER_NAME)
    if marker != {"kind": DISPOSABLE_MARKER_KIND, "nonce": nonce}:
        raise OperatorRestoreDrillError("restore_disposable_root_invalid")
    return resolved


def _require_media_root(
    media_root: Path, *, disposable_root: Path | None = None
) -> Path:
    root = media_root.resolve()
    if media_root.is_symlink() or not root.is_dir() or len(root.parts) < 3:
        raise OperatorRestoreDrillError("restore_media_root_invalid")
    if disposable_root is not None:
        try:
            root.relative_to(disposable_root)
        except ValueError as exc:
            raise OperatorRestoreDrillError("restore_media_root_invalid") from exc
        if root == disposable_root:
            raise OperatorRestoreDrillError("restore_media_root_invalid")
    return root


def _require_child_path(path: Path, root: Path) -> Path:
    if path.is_symlink():
        raise OperatorRestoreDrillError("restore_path_invalid")
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise OperatorRestoreDrillError("restore_path_invalid") from exc
    if resolved == root or resolved.parent != root:
        raise OperatorRestoreDrillError("restore_path_invalid")
    return resolved


def _resolve_media_path(root: Path, relative: str) -> Path:
    _require_derived_relative_path(relative)
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise OperatorRestoreDrillError("restore_media_inventory_invalid") from exc
    return resolved


def _require_derived_relative_path(relative: str) -> None:
    path = Path(relative)
    if (
        not relative
        or path.is_absolute()
        or ".." in path.parts
        or path.parts[0] not in DERIVED_TREES
    ):
        raise OperatorRestoreDrillError("restore_media_inventory_invalid")


def _require_replaceable_target(target: Path) -> None:
    if target.is_symlink():
        raise OperatorRestoreDrillError("restore_target_invalid")
    if target.exists() and not target.is_file():
        raise OperatorRestoreDrillError("restore_target_invalid")


def _cleanup_sidecars(target: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{target}{suffix}")
        if not sidecar.exists() and not sidecar.is_symlink():
            continue
        _require_regular_file(sidecar, "restore_sidecar_invalid")
        sidecar.unlink()


def _require_regular_file(path: Path, code: str) -> None:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise OperatorRestoreDrillError(code) from exc
    if path.is_symlink() or not stat.S_ISREG(file_stat.st_mode):
        raise OperatorRestoreDrillError(code)


def _require_non_operator_volume(volume: str) -> None:
    if volume == OPERATOR_DATABASE_VOLUME or not volume.startswith("disposable-"):
        raise OperatorRestoreDrillError("restore_database_volume_invalid")


def _validate_nonce(nonce: str) -> None:
    if not (16 <= len(nonce) <= 64) or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in nonce
    ):
        raise OperatorRestoreDrillError("restore_disposable_nonce_invalid")


def _bounded_count(conn: sqlite3.Connection, table: str) -> int:
    escaped = table.replace('"', '""')
    value = conn.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0]
    if not isinstance(value, int) or value < 0:
        raise OperatorRestoreDrillError("restore_database_inventory_invalid")
    return value


def _attestation_path(backup: Path) -> Path:
    return backup.with_name(f"{backup.name}{ATTESTATION_SUFFIX}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_owner_only_json(path: Path, payload: dict) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        os.write(descriptor, content.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_owner_only_json(path: Path) -> dict:
    _require_regular_file(path, "restore_artifact_invalid")
    file_stat = path.stat()
    if stat.S_IMODE(file_stat.st_mode) != 0o600 or file_stat.st_uid != os.getuid():
        raise OperatorRestoreDrillError("restore_artifact_permissions_invalid")
    if file_stat.st_size > 1_048_576:
        raise OperatorRestoreDrillError("restore_artifact_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OperatorRestoreDrillError("restore_artifact_invalid") from exc
    if not isinstance(value, dict):
        raise OperatorRestoreDrillError("restore_artifact_invalid")
    return value
