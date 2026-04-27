from __future__ import annotations

import hashlib
import hmac
import secrets
import time


def now_timestamp() -> str:
    """Unix timestamp in seconds. bitFlyer accepts 10- or 13-digit timestamps;
    seconds are sufficient for REST signing."""
    return str(int(time.time()))


def now_timestamp_ms() -> str:
    """Unix timestamp in milliseconds. Used for the realtime `auth` call."""
    return str(int(time.time() * 1000))


def fresh_nonce(length: int = 32) -> str:
    """Random hex string for the realtime auth nonce (16-255 chars allowed)."""
    return secrets.token_hex(length // 2)


def sign_rest(secret: str, timestamp: str, method: str, path: str, body: str) -> str:
    """Sign a REST request.

    Per bitFlyer docs: ACCESS-SIGN = HMAC-SHA256(secret,
        ACCESS-TIMESTAMP + HTTP-METHOD + REQUEST-PATH + REQUEST-BODY)
    in lowercase hex. `path` includes the leading `/v1/...` and the query
    string (if any). For methods without a body, `body` is the empty string.
    """
    msg = f"{timestamp}{method.upper()}{path}{body}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def sign_realtime(secret: str, timestamp: str, nonce: str) -> str:
    """Sign a realtime `auth` JSON-RPC call.

    signature = HMAC-SHA256(secret, timestamp + nonce) in lowercase hex.
    """
    msg = f"{timestamp}{nonce}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
