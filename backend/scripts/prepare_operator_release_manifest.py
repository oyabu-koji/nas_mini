from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services.operator_release_manifest import (
    OperatorReleaseManifestError,
    capture_compose_image_ids,
    load_image_id_source,
    write_manifest,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--compose-project", required=True)
    parser.add_argument("--database-volume", required=True)
    parser.add_argument("--disposable-nonce", required=True)
    parser.add_argument("--database-path", default="/data/mediavault.sqlite3")
    parser.add_argument("--release-env-source", type=Path, required=True)
    parser.add_argument("--rollback-env-source", type=Path, required=True)
    parser.add_argument("--rollback-image-ids-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    repository_root = Path(__file__).resolve().parents[2]
    try:
        release_image_ids = capture_compose_image_ids(
            repository_root=repository_root,
            compose_project=arguments.compose_project,
        )
        rollback_image_ids = load_image_id_source(arguments.rollback_image_ids_source)
        write_manifest(
            arguments.output,
            commit=arguments.commit,
            compose_project=arguments.compose_project,
            database_volume=arguments.database_volume,
            disposable_nonce=arguments.disposable_nonce,
            database_path=arguments.database_path,
            release_image_ids=release_image_ids,
            rollback_image_ids=rollback_image_ids,
            release_env_source=arguments.release_env_source,
            rollback_env_source=arguments.rollback_env_source,
        )
    except OperatorReleaseManifestError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    print("operator_migration_manifest_ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
