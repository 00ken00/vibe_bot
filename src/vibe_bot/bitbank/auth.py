from __future__ import annotations

import hashlib
import hmac
import time


def now_timestamp_ms() -> str:
    return str(int(time.time() * 1000))


def sign_nonce(secret: str, nonce: str, payload: str) -> str:
    """ACCESS-NONCE method.

    `payload` is:
      - GET:  "/v1/<path>?<querystring>"   (path-and-query, with leading /v1)
      - POST: raw JSON request body
    Signed string: nonce + payload. Output: lowercase hex HMAC-SHA256.
    """
    msg = f"{nonce}{payload}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def sign_time_window(
    secret: str, request_time: str, time_window: str, payload: str
) -> str:
    """ACCESS-TIME-WINDOW method (takes precedence over ACCESS-NONCE).

    Signed string: request_time + time_window + payload. Output: lowercase hex
    HMAC-SHA256. `payload` follows the same rules as `sign_nonce`.
    """
    msg = f"{request_time}{time_window}{payload}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
