from __future__ import annotations

import json
import os
import sys
import urllib.request

MAX_RESPONSE_BYTES = 65_536
CAPABILITIES_URL = "http://127.0.0.1:8000/api/v1/capabilities"


def main() -> int:
    token = os.environ.get("API_TOKEN")
    if not token:
        print("detector_v2_post_start_capability_unavailable", file=sys.stderr)
        return 1
    request = urllib.request.Request(
        CAPABILITIES_URL,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ValueError("response too large")
        document = json.loads(payload)
        if not isinstance(document, dict):
            raise TypeError("response must be an object")
        features = document.get("features")
        if not isinstance(features, dict):
            raise TypeError("features missing")
        if document.get("minimum_client_version") != "0.4.0":
            raise ValueError("client version mismatch")
        if features.get("detector_certified") is not True:
            raise ValueError("detector not certified")
        if features.get("formal_apple_log_preview") is not True:
            raise ValueError("formal preview unavailable")
        if features.get("safe_delete_candidate") is not True:
            raise ValueError("safe delete unavailable")
    except (OSError, TypeError, ValueError):
        print("detector_v2_post_start_capability_unavailable", file=sys.stderr)
        return 1
    print("detector_v2_capability_ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
