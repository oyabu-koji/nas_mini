from __future__ import annotations

import argparse
import json
import os
import sys

from app.core.settings import SettingsError, load_settings
from app.services.detector_v2_migration import (
    DetectorV2MigrationError,
    apply_detector_v2_migration,
)
from app.services.disposable_database_target import (
    DisposableDatabaseTargetError,
    require_disposable_database_target,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args(argv)
    try:
        settings = load_settings()
        require_disposable_database_target(
            database_path=settings.database_path,
            volume_name=os.environ.get("OPERATOR_DATABASE_VOLUME_NAME", ""),
            nonce=os.environ.get("OPERATOR_DISPOSABLE_NONCE", ""),
        )
        result = apply_detector_v2_migration(
            settings=settings,
            mode="preflight-only",
        )
    except (
        DisposableDatabaseTargetError,
        SettingsError,
        DetectorV2MigrationError,
    ) as exc:
        print(
            getattr(exc, "code", "detector_v2_preflight_configuration_invalid"),
            file=sys.stderr,
        )
        return 1
    except Exception:  # noqa: BLE001 - CLI output must not expose unexpected details.
        print("detector_v2_preflight_failed", file=sys.stderr)
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
