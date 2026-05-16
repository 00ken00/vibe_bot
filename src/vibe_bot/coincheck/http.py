from __future__ import annotations

import json
from typing import Any, Callable

import httpx

from .auth import now_nonce_ms, sign
from .errors import ApiError, AuthError, RateLimitError, TransportError
from .rate_limit import RateLimiter

REST_BASE = "https://coincheck.com"


class HttpClient:
    """Async HTTP transport for Coincheck REST."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        rate_limiter: RateLimiter | None = None,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
        private_trace: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._limiter = rate_limiter or RateLimiter()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._private_trace = private_trace

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
        return await self._request(method, path, params=params, signed=False)

    async def private(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> Any:
        return await self._request(
            method, path, params=params, json_body=json_body, signed=True
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        signed: bool,
    ) -> Any:
        await self._limiter.acquire()

        method = method.upper()
        body_str = ""
        headers: dict[str, str] = {}
        if json_body is not None:
            body_str = json.dumps(json_body, separators=(",", ":"))
            headers["Content-Type"] = "application/json"

        request = self._client.build_request(
            method,
            REST_BASE + path,
            params=params,
            content=body_str if json_body is not None else None,
            headers=headers,
        )

        if signed:
            if not self._api_key or not self._api_secret:
                raise AuthError(-1, message="missing api key/secret")
            nonce = now_nonce_ms()
            url = str(request.url)
            request.headers["ACCESS-KEY"] = self._api_key
            request.headers["ACCESS-NONCE"] = nonce
            request.headers["ACCESS-SIGNATURE"] = sign(
                self._api_secret, nonce, url, body_str
            )

        try:
            resp = await self._client.send(request)
        except httpx.HTTPError as e:
            raise TransportError(f"{type(e).__name__}: {e}") from e

        if signed and self._private_trace is not None:
            self._private_trace({
                "exchange": "coincheck",
                "method": method,
                "url": str(request.url),
                "params": params,
                "json_body": json_body,
                "http_status": resp.status_code,
                "raw_response": resp.text,
            })

        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> Any:
        text = resp.text
        try:
            payload = resp.json() if text else None
        except ValueError as e:
            raise TransportError(
                f"non-JSON response (HTTP {resp.status_code}): {text[:200]}"
            ) from e

        if resp.status_code == 429:
            raise RateLimitError(429, http_status=429, payload=payload)
        if resp.status_code in (401, 403):
            message = payload.get("error") if isinstance(payload, dict) else text[:200]
            raise AuthError(resp.status_code, http_status=resp.status_code, message=message, payload=payload)
        if resp.status_code >= 400:
            message = payload.get("error") if isinstance(payload, dict) else text[:200]
            raise ApiError(resp.status_code, http_status=resp.status_code, message=message, payload=payload)

        if isinstance(payload, dict) and payload.get("success") is False:
            message = payload.get("error") or payload.get("message")
            raise ApiError(-1, http_status=resp.status_code, message=message, payload=payload)

        return payload
