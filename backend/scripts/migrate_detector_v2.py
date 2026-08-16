from __future__ import annotations

import argparse
import json
import sys

from app.core.settings import SettingsError, load_settings
from app.services.detector_v2_migration import (
    DetectorV2MigrationError,
    apply_detector_v2_migration,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--isolated-database-confirmed", action="store_true")
    parser.add_argument("--offline-maintenance-confirmed", action="store_true")
    parser.add_argument("--api-stopped-confirmed", action="store_true")
    parser.add_argument("--release-040-ready-confirmed", action="store_true")
    arguments = parser.parse_args(argv)
    selected_mode = (
        "apply"
        if arguments.apply
        else "dry-run"
        if arguments.dry_run
        else "preflight-only"
    )
    try:
        result = apply_detector_v2_migration(
            settings=load_settings(),
            mode=selected_mode,
            isolated_database_confirmed=arguments.isolated_database_confirmed,
            offline_maintenance_confirmed=arguments.offline_maintenance_confirmed,
            api_stopped_confirmed=arguments.api_stopped_confirmed,
            release_040_ready_confirmed=arguments.release_040_ready_confirmed,
        )
    except (SettingsError, DetectorV2MigrationError) as exc:
        print(
            getattr(exc, "code", "detector_v2_migration_configuration_invalid"),
            file=sys.stderr,
        )
        return 1
    except Exception:
        print("detector_v2_migration_failed", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"status": result.status},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
