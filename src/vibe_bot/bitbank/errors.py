from __future__ import annotations


class BitbankError(Exception):
    """Base class for all bitbank client errors."""


class TransportError(BitbankError):
    """Network-level failure (connection, timeout, DNS, non-JSON response)."""


class ApiError(BitbankError):
    """bitbank returned a non-success envelope.

    bitbank wraps every response in `{"success": 0|1, "data": ...}`. When
    `success == 0`, `data.code` carries a numeric error code. We preserve the
    code (and HTTP status) for programmatic handling.
    """

    def __init__(self, code: int, http_status: int | None = None, message: str | None = None) -> None:
        self.code = code
        self.http_status = http_status
        self.message = message or _CODE_MESSAGES.get(code, "")
        text = self.message or "no message"
        super().__init__(f"bitbank API error code={code}: {text}")


class AuthError(ApiError):
    """API-KEY / signature / nonce / time-window rejected."""


class RateLimitError(ApiError):
    """Throttled by bitbank (HTTP 429 or rate-limit error code)."""


# Subset of the official codes (errors.md). Used to pick the right exception
# subclass and to give callers a readable string without a docs lookup.
_CODE_MESSAGES: dict[int, str] = {
    10000: "URL not found",
    10001: "system error",
    10005: "response timeout",
    10007: "system maintenance",
    10008: "server is busy, retry later",
    10009: "too many requests, retry later with reduced rate",
    20001: "API authorization failure",
    20002: "invalid ACCESS-KEY",
    20003: "ACCESS-KEY missing",
    20004: "ACCESS-NONCE missing",
    20005: "invalid ACCESS-SIGNATURE",
    20011: "MFA authentication failed",
    20014: "SMS verification failed",
    20018: "request missing /v1/ path component",
    20019: "request missing /v1/ path component",
    20023: "OTP code absent",
    20024: "SMS code absent",
    20025: "both OTP and SMS code absent",
    20026: "MFA temporarily locked, retry after 60 seconds",
    20033: "ACCESS-REQUEST-TIME header missing",
    20034: "ACCESS-REQUEST-TIME value invalid",
    20035: "no request sent within ACCESS-TIME-WINDOW",
    20039: "invalid ACCESS-NONCE value",
}

AUTH_CODES: frozenset[int] = frozenset({
    20001, 20002, 20003, 20004, 20005, 20018, 20019, 20033, 20034, 20035, 20039,
})

RATE_LIMIT_CODES: frozenset[int] = frozenset({10008, 10009})
