from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services.detector_fixture_descriptor import (
    LOCAL_DESCRIPTOR_REPOSITORY_PATH,
    confine_fixture_path,
    load_local_fixture_descriptor,
    validate_fixture_root,
)
from app.services.detector_manifest import DetectorValidationError
from app.services.external_fixture_git_audit import (
    ExternalFixture,
    ExternalFixtureGitAuditError,
    audit_external_fixture_git_history,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.parse_args(argv)
    fixture_root = REPOSITORY_ROOT / "data"
    descriptor_path = REPOSITORY_ROOT.joinpath(
        *LOCAL_DESCRIPTOR_REPOSITORY_PATH.parts
    )
    try:
        validate_fixture_root(fixture_root)
        descriptor = load_local_fixture_descriptor(descriptor_path)
        fixtures = tuple(
            ExternalFixture(
                path=confine_fixture_path(fixture_root, item.path),
                expected_sha256=item.expected_sha256,
            )
            for item in descriptor.fixtures
        )
        result = audit_external_fixture_git_history(
            repository_root=REPOSITORY_ROOT,
            fixtures=fixtures,
        )
    except (DetectorValidationError, ExternalFixtureGitAuditError) as exc:
        print(exc.code, file=sys.stderr)
        return 1

    print(
        "external_fixture_git_audit_ok "
        f"fixture_count={result.fixture_count} "
        f"reachable_record_count={result.reachable_record_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
