from __future__ import annotations


class GMOError(Exception):
    """Base class for all GMO Coin client errors."""


class TransportError(GMOError):
    """Network-level failure (connection, timeout, DNS)."""


class ApiError(GMOError):
    """GMO returned a non-zero status code in the response envelope.

    GMO wraps every response in `{"status": int, "data": ..., "messages": [...]}`.
    Any `status != 0` raises this; per-message `message_code` and `message_string`
    are preserved for programmatic handling.
    """

    def __init__(
        self,
        status: int,
        messages: list[dict],
        http_status: int | None = None,
    ) -> None:
        self.status = status
        self.messages = messages
        self.http_status = http_status
        codes = ",".join(m.get("message_code", "") for m in messages) or "?"
        text = "; ".join(m.get("message_string", "") for m in messages) or "no message"
        super().__init__(f"GMO API error status={status} codes=[{codes}]: {text}")


class AuthError(ApiError):
    """API-KEY / API-SIGN / timestamp rejected."""


class RateLimitError(ApiError):
    """Throttled by GMO (HTTP 429 or rate-limit error code)."""
