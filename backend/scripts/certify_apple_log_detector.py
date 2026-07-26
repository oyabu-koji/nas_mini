from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services.bounded_subprocess import BoundedProcessError
from app.services.detector_certification import certify_detector
from app.services.detector_manifest import DetectorValidationError


EXPECTED_RULE_INPUT = Path(
    "assets/detectors/apple-log-v1/detector-rule-input-v1.json"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--rule-input", required=True)
    parser.add_argument("--fixture-root", required=True)
    arguments = parser.parse_args(argv)

    backend_root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != backend_root:
        print("detector_certification_invalid_working_directory", file=sys.stderr)
        return 2
    if Path(arguments.rule_input) != EXPECTED_RULE_INPUT:
        print("log_detector_manifest_invalid", file=sys.stderr)
        return 2
    fixture_root = Path(arguments.fixture_root)
    if not fixture_root.is_absolute():
        print("log_detector_manifest_invalid", file=sys.stderr)
        return 2

    try:
        result = certify_detector(
            rule_input_path=backend_root / EXPECTED_RULE_INPUT,
            fixture_root=fixture_root,
        )
    except (DetectorValidationError, BoundedProcessError) as exc:
        print(exc.code, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("log_probe_failed", file=sys.stderr)
        return 130

    print(f"manifest_sha256={result.manifest_sha256}")
    print(f"rule_input_sha256={result.rule_input_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
