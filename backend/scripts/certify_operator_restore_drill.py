from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from app.services.offline_startup_migration import (
    MIGRATION_CONTRACT,
    MIGRATIONS_DIR,
    _apply_one,
    apply_offline_startup_migrations,
)
from app.services.operator_restore_drill import (
    create_fresh_backup,
    initialize_mounted_disposable_root,
    run_restore_drill,
    snapshot_media,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Certify restore in a disposable volume"
    )
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--database-volume", required=True)
    arguments = parser.parse_args(argv)
    root = Path("/restore")
    initialize_mounted_disposable_root(
        root,
        nonce=arguments.nonce,
        database_volume=arguments.database_volume,
    )
    database = root / "mediavault.sqlite3"
    _initialize_001(database)
    apply_offline_startup_migrations(
        database_path=database,
        offline_maintenance_confirmed=True,
    )
    media_root = root / "media"
    for directory in ("originals", "previews", "thumbnails"):
        (media_root / directory).mkdir(parents=True, exist_ok=False)
    original = media_root / "originals/disposable.bin"
    original.write_bytes(b"disposable-original")
    protected = snapshot_media(media_root)
    backup = root / "pre-release.sqlite3"
    create_fresh_backup(
        source_database=database,
        backup_database=backup,
        disposable_root=root,
        nonce=arguments.nonce,
        database_volume=arguments.database_volume,
    )
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE operation_fault (id INTEGER)")
        conn.commit()
    orphan = media_root / "previews/operation-orphan.bin"
    orphan.write_bytes(b"operation-orphan")
    Path(f"{database}-wal").write_bytes(b"stale-wal")
    Path(f"{database}-shm").write_bytes(b"stale-shm")
    result = run_restore_drill(
        backup_database=backup,
        target_database=database,
        disposable_root=root,
        nonce=arguments.nonce,
        database_volume=arguments.database_volume,
        media_root=media_root,
        protected_before=protected,
        operation_derived_paths=("previews/operation-orphan.bin",),
    )
    if original.read_bytes() != b"disposable-original":
        raise RuntimeError("restore_media_protected_changed")
    if Path(f"{database}-wal").exists() or Path(f"{database}-shm").exists():
        raise RuntimeError("restore_sidecar_invalid")
    print(
        json.dumps(
            {
                "status": result.status,
                "schema_version": result.schema_version,
                "removed_operation_orphans": result.removed_operation_orphans,
            },
            sort_keys=True,
        )
    )
    return 0


def _initialize_001(database: Path) -> None:
    conn = sqlite3.connect(database)
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    version = MIGRATION_CONTRACT[0][0]
    sql = (MIGRATIONS_DIR / f"{version}.sql").read_text(encoding="utf-8")
    _apply_one(conn, version=version, sql=sql, fault_injector=None)
    conn.close()


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(main())
