from __future__ import annotations

import os
import sys
from pathlib import Path

from app.services.disposable_database_target import (
    DisposableDatabaseTargetError,
    initialize_disposable_database_target,
)


def main() -> int:
    try:
        initialize_disposable_database_target(
            database_path=Path(os.environ.get("DATABASE_PATH", "")),
            volume_name=os.environ.get("OPERATOR_DATABASE_VOLUME_NAME", ""),
            nonce=os.environ.get("OPERATOR_DISPOSABLE_NONCE", ""),
        )
    except DisposableDatabaseTargetError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    print("operator_disposable_database_ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
