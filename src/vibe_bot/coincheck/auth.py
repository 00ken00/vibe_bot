from __future__ import annotations

import hashlib
import hmac
import time


def now_nonce_ms() -> str:
    """Return a millisecond nonce suitable for Coincheck ACCESS-NONCE."""
    return str(int(time.time() * 1000))


def sign(secret: str, nonce: str, url: str, body: str = "") -> str:
    """Compute Coincheck ACCESS-SIGNATURE.

    Coincheck signs: ACCESS-NONCE + full request URL + raw request body.
    The URL includes the scheme, host, path, and query string when present.
    """
    payload = f"{nonce}{url}{body}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
