from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from app.services.disposable_database_target import (
    DisposableDatabaseTargetError,
    claim_disposable_database_operation,
    require_disposable_database_target,
)
from scripts.migrate_phase2c_safe_delete_candidate import main as migration_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--offline-maintenance-confirmed", action="store_true", required=True
    )
    arguments = parser.parse_args(argv)
    database_path = Path(os.environ.get("DATABASE_PATH", ""))
    nonce = os.environ.get("OPERATOR_DISPOSABLE_NONCE", "")
    try:
        require_disposable_database_target(
            database_path=database_path,
            volume_name=os.environ.get("OPERATOR_DATABASE_VOLUME_NAME", ""),
            nonce=nonce,
        )
        claim_disposable_database_operation(
            database_path=database_path,
            nonce=nonce,
            operation="phase2c-009-dry-run"
            if arguments.dry_run
            else "phase2c-009-apply",
        )
    except DisposableDatabaseTargetError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    selected = "--dry-run" if arguments.dry_run else "--apply"
    return migration_main([selected, "--offline-maintenance-confirmed"])


if __name__ == "__main__":
    raise SystemExit(main())
