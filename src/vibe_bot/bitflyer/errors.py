from __future__ import annotations


class BitflyerError(Exception):
    """Base class for all bitFlyer client errors."""


class TransportError(BitflyerError):
    """Network-level failure (connection, timeout, DNS, non-JSON response)."""


class ApiError(BitflyerError):
    """bitFlyer returned an error envelope or non-2xx HTTP status.

    bitFlyer error responses look like
    `{"status": -<n>, "error_message": "...", "data": null}` where the negative
    `status` is the machine-readable code. We preserve that code (and the HTTP
    status, if any) for programmatic handling.
    """

    def __init__(
        self,
        status: int,
        http_status: int | None = None,
        message: str | None = None,
    ) -> None:
        self.status = status
        self.http_status = http_status
        self.message = message or _STATUS_MESSAGES.get(status, "")
        text = self.message or "no message"
        super().__init__(f"bitFlyer API error status={status}: {text}")


class AuthError(ApiError):
    """API-KEY / signature / timestamp / 2FA rejected."""


class RateLimitError(ApiError):
    """Throttled by bitFlyer (HTTP 429 or rate-limit error status)."""


# Subset of documented and observed status codes. Used to pick the right
# exception subclass and give callers a readable string without a docs lookup.
_STATUS_MESSAGES: dict[int, str] = {
    -100: "end of life",
    -101: "request timeout",
    -102: "server is busy",
    -104: "request body too large",
    -107: "invalid request",
    -108: "request blocked",
    -110: "invalid parameter",
    -111: "resource not found",
    -112: "key not found",
    -113: "service unavailable",
    -114: "method not allowed",
    -118: "invalid HTTP path",
    -132: "rate limit exceeded",
    -133: "rate limit exceeded (orders)",
    -200: "permission denied",
    -201: "API key not authenticated",
    -205: "request signature mismatch",
    -208: "API key expired",
    -300: "insufficient balance",
    -500: "withdrawal not allowed",
    -505: "two-factor authentication code is incorrect",
    -700: "account not yet authenticated",
}

AUTH_STATUSES: frozenset[int] = frozenset({-200, -201, -205, -208, -505, -700})

RATE_LIMIT_STATUSES: frozenset[int] = frozenset({-102, -108, -132, -133})
