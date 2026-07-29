import argparse
import json
import sys

from app.core.settings import SettingsError, load_settings
from app.services.safe_delete_reconciliation import (
    reconcile_safe_delete_candidates,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = reconcile_safe_delete_candidates(
            settings=load_settings(),
            apply_changes=args.apply,
        )
    except (SettingsError, RuntimeError):
        print("phase2c_reconciliation_failed", file=sys.stderr)
        return 1
    except Exception:
        print("phase2c_reconciliation_failed", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": result.status,
                "examined": result.examined,
                "promoted": result.promoted,
                "demoted": result.demoted,
                "unchanged": result.unchanged,
                "reasons": result.reasons,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
