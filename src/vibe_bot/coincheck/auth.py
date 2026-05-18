from __future__ import annotations

import hashlib
import hmac
import threading
import time

_NONCE_LOCK = threading.Lock()
_LAST_NONCE = 0


def now_nonce_ms() -> str:
    """Return a monotonically increasing Coincheck ACCESS-NONCE."""
    global _LAST_NONCE
    with _NONCE_LOCK:
        nonce = max(time.time_ns() // 1_000, _LAST_NONCE + 1)
        _LAST_NONCE = nonce
        return str(nonce)


def sign(secret: str, nonce: str, url: str, body: str = "") -> str:
    """Compute Coincheck ACCESS-SIGNATURE.

    Coincheck signs: ACCESS-NONCE + full request URL + raw request body.
    The URL includes the scheme, host, path, and query string when present.
    """
    payload = f"{nonce}{url}{body}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
