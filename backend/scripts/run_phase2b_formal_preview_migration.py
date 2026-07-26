from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services.phase2b_host_migration import (
    Phase2BHostMigrationError,
    run_phase2b_host_migration,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.apply:
        print("phase2b_migration_apply_confirmation_required", file=sys.stderr)
        return 2

    backend_root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != backend_root:
        print("phase2b_migration_invalid_working_directory", file=sys.stderr)
        return 2
    try:
        run_phase2b_host_migration(repository_root=backend_root.parent)
    except Phase2BHostMigrationError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    print("phase2b_migration_complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
