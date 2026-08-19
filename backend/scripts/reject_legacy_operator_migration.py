from __future__ import annotations

import sys


def main() -> int:
    print("legacy_operator_migration_wrapper_disabled", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
