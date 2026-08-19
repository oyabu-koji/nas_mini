from __future__ import annotations

import os
import sys
from pathlib import Path

from app.services.disposable_database_target import (
    DisposableDatabaseTargetError,
    claim_disposable_database_operation,
    require_disposable_database_target,
)


def main() -> int:
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
            operation="worker-drain-008",
        )
    except DisposableDatabaseTargetError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    from app.workers.worker import run_forever

    run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
