from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.services.operator_release_manifest import OperatorReleaseManifestError
from app.services.operator_release_orchestration import (
    OperatorReleaseOrchestrationError,
    run_operator_release_orchestration,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.execute:
        print("operator_migration_execute_confirmation_required", file=sys.stderr)
        return 2
    repository_root = Path(__file__).resolve().parents[2]
    try:
        result = run_operator_release_orchestration(
            repository_root=repository_root,
            manifest_path=arguments.manifest,
        )
    except (OperatorReleaseManifestError, OperatorReleaseOrchestrationError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": exc.code,
                    "last_committed_version": getattr(
                        exc, "last_committed_version", None
                    ),
                    "restore_required": getattr(exc, "restore_required", False),
                    "services_stopped": getattr(exc, "services_stopped", False),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": result.status,
                "completed_phases": result.completed_phases,
                "services_stopped": result.services_stopped,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
