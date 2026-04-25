from __future__ import annotations

import json
from typing import Any

import httpx

from .auth import now_timestamp_ms, sign
from .errors import ApiError, AuthError, RateLimitError, TransportError
from .rate_limit import RateLimiter

PUBLIC_BASE = "https://api.coin.z.com/public"
PRIVATE_BASE = "https://api.coin.z.com/private"

# GMO error codes that should map to specific exception subclasses.
_AUTH_CODES = {"ERR-5106", "ERR-5114", "ERR-5115", "ERR-5116", "ERR-5122", "ERR-5123"}
_RATE_LIMIT_CODES = {"ERR-5003", "ERR-5008", "ERR-5009"}


class HttpClient:
    """Async HTTP transport for GMO REST. Handles signing, rate limiting, error mapping."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        rate_limiter: RateLimiter | None = None,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._limiter = rate_limiter or RateLimiter()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> None:
        del exc_type, exc, tb
        await self.aclose()

    async def public(
        self, method: str, path: str, *, params: dict | None = None
    ) -> Any:
        return await self._request(
            method, PUBLIC_BASE + path, path, params=params, signed=False
        )

    async def private(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        sign_body_override: str | None = None,
    ) -> Any:
        """sign_body_override: if set, use this string (typically "") as the body
        component of the HMAC signature instead of the actual JSON body. Required
        for ws-auth PUT/DELETE which send a body but GMO signs as if empty.
        """
        return await self._request(
            method,
            PRIVATE_BASE + path,
            path,
            params=params,
            json_body=json_body,
            signed=True,
            sign_body_override=sign_body_override,
        )

    async def _request(
        self,
        method: str,
        url: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        signed: bool,
        sign_body_override: str | None = None,
    ) -> Any:
        await self._limiter.acquire()

        headers: dict[str, str] = {}
        body_str = ""
        if json_body is not None:
            body_str = json.dumps(json_body, separators=(",", ":"))
            headers["Content-Type"] = "application/json"

        if signed:
            if not self._api_key or not self._api_secret:
                raise AuthError(
                    -1,
                    [{"message_code": "LOCAL", "message_string": "missing api key/secret"}],
                )
            ts = now_timestamp_ms()
            sign_body = sign_body_override if sign_body_override is not None else body_str
            headers["API-KEY"] = self._api_key
            headers["API-TIMESTAMP"] = ts
            headers["API-SIGN"] = sign(self._api_secret, ts, method, path, sign_body)

        try:
            resp = await self._client.request(
                method,
                url,
                params=params,
                content=body_str if json_body is not None else None,
                headers=headers,
            )
        except httpx.HTTPError as e:
            raise TransportError(str(e)) from e

        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> Any:
        try:
            envelope = resp.json()
        except ValueError as e:
            raise TransportError(f"non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}") from e

        status = envelope.get("status")
        messages = envelope.get("messages") or []

        if resp.status_code == 429 or status != 0:
            codes = {m.get("message_code") for m in messages}
            if resp.status_code == 429 or codes & _RATE_LIMIT_CODES:
                raise RateLimitError(status or -1, messages, http_status=resp.status_code)
            if codes & _AUTH_CODES:
                raise AuthError(status or -1, messages, http_status=resp.status_code)
            if status != 0:
                raise ApiError(status, messages, http_status=resp.status_code)

        return envelope.get("data")
