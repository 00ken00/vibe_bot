from __future__ import annotations

import hashlib
import hmac
import time


def now_timestamp_ms() -> str:
    return str(int(time.time() * 1000))


def sign(secret: str, timestamp: str, method: str, path: str, body: str = "") -> str:
    """Compute API-SIGN per GMO spec.

    Concatenation order: timestamp + METHOD + path + body
    - `path` starts with `/v1`, never `/private`.
    - `body` is the raw JSON string for POST/PUT, empty string for GET/DELETE.
    - Query string is NOT included in the signature.
    Output: lowercase hex HMAC-SHA256.
    """
    payload = f"{timestamp}{method.upper()}{path}{body}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
