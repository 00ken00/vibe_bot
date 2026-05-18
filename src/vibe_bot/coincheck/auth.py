from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from pathlib import Path
from typing import TextIO

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback for portability.
    fcntl = None  # type: ignore[assignment]

_NONCE_LOCK = threading.Lock()
_LAST_NONCE = 0
_NONCE_PATH = Path(
    os.environ.get(
        "COINCHECK_NONCE_FILE",
        str(Path.home() / ".cache" / "vibe_bot" / "coincheck_nonce"),
    )
)
_NONCE_FLOOR = 0
try:
    _NONCE_FLOOR = int(os.environ.get("COINCHECK_NONCE_FLOOR", "0") or "0")
except ValueError:
    _NONCE_FLOOR = 0


def now_nonce_ms() -> str:
    """Return a monotonically increasing Coincheck ACCESS-NONCE."""
    global _LAST_NONCE
    with _NONCE_LOCK:
        nonce = _next_persisted_nonce()
        _LAST_NONCE = nonce
        return str(nonce)


def _next_persisted_nonce() -> int:
    try:
        _NONCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _NONCE_PATH.open("a+") as nonce_file:
            _lock_nonce_file(nonce_file)
            nonce_file.seek(0)
            persisted_nonce = _parse_nonce(nonce_file.read())
            nonce = _next_nonce(persisted_nonce)
            nonce_file.seek(0)
            nonce_file.truncate()
            nonce_file.write(str(nonce))
            nonce_file.flush()
            os.fsync(nonce_file.fileno())
            return nonce
    except OSError:
        return _next_nonce(0)


def _lock_nonce_file(nonce_file: TextIO) -> None:
    if fcntl is not None:
        fcntl.flock(nonce_file.fileno(), fcntl.LOCK_EX)


def _parse_nonce(value: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        return 0


def _next_nonce(persisted_nonce: int) -> int:
    return max(
        int(time.time() * 1000),
        _LAST_NONCE + 1,
        persisted_nonce + 1,
        _NONCE_FLOOR,
    )


def sign(secret: str, nonce: str, url: str, body: str = "") -> str:
    """Compute Coincheck ACCESS-SIGNATURE.

    Coincheck signs: ACCESS-NONCE + full request URL + raw request body.
    The URL includes the scheme, host, path, and query string when present.
    """
    payload = f"{nonce}{url}{body}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
