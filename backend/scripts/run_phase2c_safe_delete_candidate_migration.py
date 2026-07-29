from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services.phase2c_host_migration import (
    Phase2CHostMigrationError,
    run_phase2c_host_migration,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    arguments = parser.parse_args(argv)

    backend_root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != backend_root:
        print("phase2c_migration_invalid_working_directory", file=sys.stderr)
        return 2
    try:
        run_phase2c_host_migration(
            repository_root=backend_root.parent,
            mode="apply" if arguments.apply else "dry-run",
        )
    except Phase2CHostMigrationError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    print("phase2c_migration_complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
