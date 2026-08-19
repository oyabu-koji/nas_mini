from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from app.services.disposable_database_target import (
    DisposableDatabaseTargetError,
    claim_disposable_database_operation,
    require_disposable_database_target,
)
from app.services.offline_startup_migration import (
    OfflineStartupMigrationError,
    apply_offline_startup_migrations,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--offline-maintenance-confirmed", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.apply:
        print("offline_migration_apply_confirmation_required", file=sys.stderr)
        return 2
    database_value = os.environ.get("DATABASE_PATH", "").strip()
    if not database_value:
        print("offline_migration_configuration_invalid", file=sys.stderr)
        return 2
    try:
        require_disposable_database_target(
            database_path=Path(database_value),
            volume_name=os.environ.get("OPERATOR_DATABASE_VOLUME_NAME", ""),
            nonce=os.environ.get("OPERATOR_DISPOSABLE_NONCE", ""),
        )
        claim_disposable_database_operation(
            database_path=Path(database_value),
            nonce=os.environ.get("OPERATOR_DISPOSABLE_NONCE", ""),
            operation="offline-002-007",
        )
        result = apply_offline_startup_migrations(
            database_path=Path(database_value),
            offline_maintenance_confirmed=arguments.offline_maintenance_confirmed,
        )
    except (DisposableDatabaseTargetError, OfflineStartupMigrationError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": exc.code,
                    "last_committed_version": getattr(
                        exc, "last_committed_version", None
                    ),
                    "restore_required": getattr(exc, "restore_required", False),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": result.status,
                "last_committed_version": result.last_committed_version,
                "applied_count": result.applied_count,
                "restore_required": result.restore_required,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
