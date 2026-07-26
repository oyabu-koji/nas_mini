from __future__ import annotations

import hashlib
from typing import Any

import rfc8785


class CanonicalJsonError(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, UnicodeError, ValueError, TypeError) as exc:
        raise CanonicalJsonError() from exc


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
