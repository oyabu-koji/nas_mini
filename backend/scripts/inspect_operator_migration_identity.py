from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from app.services.disposable_database_target import (
    DisposableDatabaseTargetError,
    require_disposable_database_target,
)
from app.services.operator_migration_identity import (
    OperatorMigrationIdentityError,
    read_operator_migration_identity,
)


def main() -> int:
    database_path = Path(os.environ.get("DATABASE_PATH", ""))
    try:
        require_disposable_database_target(
            database_path=database_path,
            volume_name=os.environ.get("OPERATOR_DATABASE_VOLUME_NAME", ""),
            nonce=os.environ.get("OPERATOR_DISPOSABLE_NONCE", ""),
        )
        identity = read_operator_migration_identity(database_path)
    except (DisposableDatabaseTargetError, OperatorMigrationIdentityError) as exc:
        print(exc.code, file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "identity_verified",
                "last_committed_version": identity.last_committed_version,
                "migration_count": identity.migration_count,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
