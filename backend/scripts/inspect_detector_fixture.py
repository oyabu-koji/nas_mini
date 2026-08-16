from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services.bounded_subprocess import BoundedProcessError
from app.services.detector_inspection import inspect_fixture_path, serialize_inspection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    arguments = parser.parse_args(argv)
    try:
        result = inspect_fixture_path(Path(arguments.fixture))
        sys.stdout.buffer.write(serialize_inspection(result))
    except BoundedProcessError as exc:
        sys.stderr.write(exc.code + "\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
