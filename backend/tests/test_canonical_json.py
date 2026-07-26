import hashlib

import pytest

from app.services.canonical_json import CanonicalJsonError, canonical_json_bytes, sha256_hex


def test_canonical_json_and_sha256_are_shared_deterministic_primitives():
    canonical = canonical_json_bytes({"z": 1, "a": "色"})

    assert canonical == '{"a":"色","z":1}'.encode()
    assert sha256_hex(canonical) == hashlib.sha256(canonical).hexdigest()


def test_canonical_json_rejects_unrepresentable_value():
    with pytest.raises(CanonicalJsonError):
        canonical_json_bytes({"value": float("nan")})
