from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.services.operator_release_manifest import OperatorReleaseManifestError
from app.services.operator_release_orchestration import (
    OperatorReleaseOrchestrationError,
    validate_rollback_reconstruction,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        services = validate_rollback_reconstruction(manifest_path=arguments.manifest)
    except (OperatorReleaseManifestError, OperatorReleaseOrchestrationError) as exc:
        print(exc.code, file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"status": "rollback_reconstruction_valid", "services": services},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
