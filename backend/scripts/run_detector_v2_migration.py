from __future__ import annotations

import sys
from pathlib import Path

from app.services.detector_v2_host_migration import (
    DetectorV2HostMigrationError,
    run_detector_v2_host_migration,
)


def main() -> int:
    backend_root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != backend_root:
        print("detector_v2_migration_invalid_working_directory", file=sys.stderr)
        return 2
    try:
        run_detector_v2_host_migration(repository_root=backend_root.parent)
    except DetectorV2HostMigrationError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    print("detector_v2_migration_complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
