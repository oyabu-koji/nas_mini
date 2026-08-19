from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.operator_restore_drill import (
    MediaFileIdentity,
    OperatorRestoreDrillError,
    run_restore_drill,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a disposable restore drill")
    parser.add_argument("--backup-database", type=Path, required=True)
    parser.add_argument("--target-database", type=Path, required=True)
    parser.add_argument("--disposable-root", type=Path, required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--database-volume", required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--protected-inventory", type=Path, required=True)
    parser.add_argument("--operation-derived-inventory", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("--execute is required")
    try:
        protected = _load_protected(args.protected_inventory)
        operation = _load_operation(args.operation_derived_inventory)
        result = run_restore_drill(
            backup_database=args.backup_database,
            target_database=args.target_database,
            disposable_root=args.disposable_root,
            nonce=args.nonce,
            database_volume=args.database_volume,
            media_root=args.media_root,
            protected_before=protected,
            operation_derived_paths=operation,
        )
    except (OperatorRestoreDrillError, OSError, ValueError) as exc:
        code = getattr(exc, "code", "restore_artifact_invalid")
        print(json.dumps({"status": "failed", "reason": code}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "status": result.status,
                "schema_version": result.schema_version,
                "table_count": result.table_count,
                "protected_media_count": result.protected_media_count,
                "removed_operation_orphans": result.removed_operation_orphans,
            },
            sort_keys=True,
        )
    )
    return 0


def _load_protected(path: Path) -> tuple[MediaFileIdentity, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) > 10_000:
        raise ValueError
    return tuple(MediaFileIdentity(**item) for item in payload)


def _load_operation(path: Path) -> tuple[str, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) > 10_000:
        raise ValueError
    return tuple(str(item) for item in payload)


if __name__ == "__main__":
    raise SystemExit(main())
