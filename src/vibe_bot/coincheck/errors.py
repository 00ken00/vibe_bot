from __future__ import annotations


class CoincheckError(Exception):
    """Base class for all Coincheck client errors."""


class TransportError(CoincheckError):
    """Network-level failure (connection, timeout, DNS)."""


class ApiError(CoincheckError):
    """Coincheck returned an API error or non-2xx HTTP status."""

    def __init__(
        self,
        status: int,
        *,
        http_status: int | None = None,
        message: str | None = None,
        payload: object | None = None,
    ) -> None:
        self.status = status
        self.http_status = http_status
        self.message = message
        self.payload = payload
        if message is not None:
            detail = message
        elif payload is not None:
            detail = str(payload)[:200]
        else:
            detail = "no message"
        super().__init__(f"Coincheck API error status={status}: {detail}")


class AuthError(ApiError):
    """ACCESS-KEY / ACCESS-SIGNATURE / nonce rejected."""


class RateLimitError(ApiError):
    """Throttled by Coincheck (HTTP 429)."""
