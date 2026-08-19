from __future__ import annotations

import argparse
import json
import sys

from app.core.settings import SettingsError, load_settings
from app.services.disposable_database_target import (
    DisposableDatabaseTargetError,
    require_disposable_container_database,
)
from app.services.phase2c_migration import (
    Phase2CMigrationError,
    apply_phase2c_migration,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--offline-maintenance-confirmed", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        settings = load_settings()
        require_disposable_container_database(settings)
        result = apply_phase2c_migration(
            settings=settings,
            offline_maintenance_confirmed=arguments.offline_maintenance_confirmed,
            dry_run=arguments.dry_run,
        )
    except (
        DisposableDatabaseTargetError,
        SettingsError,
        Phase2CMigrationError,
    ) as exc:
        print(
            getattr(
                exc,
                "code",
                "phase2c_migration_configuration_invalid",
            ),
            file=sys.stderr,
        )
        return 1
    except Exception:  # noqa: BLE001 - routine output must remain sanitized.
        print("phase2c_migration_failed", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": result.status,
                "promoted": result.promoted,
                "skipped": result.skipped,
                "reasons": result.reasons,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
