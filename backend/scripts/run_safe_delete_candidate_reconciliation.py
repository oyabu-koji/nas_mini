from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services.safe_delete_reconciliation_host import (
    SafeDeleteReconciliationHostError,
    run_safe_delete_reconciliation_host,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    arguments = parser.parse_args(argv)

    backend_root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != backend_root:
        print(
            "phase2c_reconciliation_invalid_working_directory",
            file=sys.stderr,
        )
        return 2
    try:
        run_safe_delete_reconciliation_host(
            repository_root=backend_root.parent,
            mode="apply" if arguments.apply else "dry-run",
        )
    except SafeDeleteReconciliationHostError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    print("phase2c_reconciliation_complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
