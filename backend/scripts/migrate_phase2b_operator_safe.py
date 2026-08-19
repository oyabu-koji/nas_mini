from __future__ import annotations

import os
import sys
from pathlib import Path

from app.services.disposable_database_target import (
    DisposableDatabaseTargetError,
    claim_disposable_database_operation,
    require_disposable_database_target,
)
from scripts.migrate_phase2b_formal_preview import main as migration_main


def main() -> int:
    try:
        _require_target()
    except DisposableDatabaseTargetError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    return migration_main(["--apply", "--offline-maintenance-confirmed"])


def _require_target() -> None:
    database_path = Path(os.environ.get("DATABASE_PATH", ""))
    nonce = os.environ.get("OPERATOR_DISPOSABLE_NONCE", "")
    require_disposable_database_target(
        database_path=database_path,
        volume_name=os.environ.get("OPERATOR_DATABASE_VOLUME_NAME", ""),
        nonce=nonce,
    )
    claim_disposable_database_operation(
        database_path=database_path,
        nonce=nonce,
        operation="phase2b-008",
    )


if __name__ == "__main__":
    raise SystemExit(main())
