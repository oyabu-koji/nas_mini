from __future__ import annotations

import argparse
import sys

from app.core.settings import SettingsError, load_settings
from app.services.initial_release_guard import InitialReleaseConfigurationError
from app.services.phase2b_migration import (
    Phase2BMigrationError,
    apply_phase2b_migration,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--offline-maintenance-confirmed", action="store_true")
    arguments = parser.parse_args(argv)

    try:
        settings = load_settings()
        result = apply_phase2b_migration(
            settings=settings,
            offline_maintenance_confirmed=arguments.offline_maintenance_confirmed,
            preflight_only=arguments.preflight_only,
        )
    except InitialReleaseConfigurationError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    except (SettingsError, Phase2BMigrationError) as exc:
        code = getattr(exc, "code", "phase2b_migration_configuration_invalid")
        print(code, file=sys.stderr)
        return 1
    print(result.status)
    print(f"schema_sql_sha256={result.schema_sql_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
