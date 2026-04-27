from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import httpx

from .auth import now_timestamp, sign_rest
from .errors import (
    AUTH_STATUSES,
    RATE_LIMIT_STATUSES,
    ApiError,
    AuthError,
    RateLimitError,
    TransportError,
)
from .rate_limit import RateLimiter

REST_BASE = "https://api.bitflyer.com"
REST_PATH_PREFIX = "/v1"


class HttpClient:
    """Async HTTP transport for bitFlyer REST. Handles signing, rate limiting,
    and error mapping.

    bitFlyer signs:  ACCESS-TIMESTAMP + METHOD + PATH(+?query) + BODY
    The query string is part of the signed PATH on GET requests; the body is
    the raw JSON sent on POSTs (empty string for body-less calls).
    """

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
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
    ) -> Any:
        """Public REST. `path` is the bare resource path (e.g. '/getticker');
        the `/v1` prefix is added here."""
        full_path = REST_PATH_PREFIX + path
        return await self._request(method, full_path, params=params, signed=False)

    async def private(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | list | None = None,
    ) -> Any:
        """Private REST. `path` is the bare resource path (e.g. '/me/getbalance');
        the `/v1` prefix is added here AND included in the signature payload."""
        full_path = REST_PATH_PREFIX + path
        return await self._request(
            method, full_path, params=params, json_body=json_body, signed=True
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | list | None = None,
        signed: bool,
    ) -> Any:
        await self._limiter.acquire()

        method = method.upper()
        body_str = ""
        headers: dict[str, str] = {}
        if json_body is not None:
            body_str = json.dumps(json_body, separators=(",", ":"))
            headers["Content-Type"] = "application/json"

        # Query string is part of the signed path on GET.
        signed_path = path
        if params:
            qs = urlencode(params, doseq=True)
            if qs:
                signed_path = f"{path}?{qs}"

        if signed:
            if not self._api_key or not self._api_secret:
                raise AuthError(-201, message="missing api key/secret")
            ts = now_timestamp()
            headers["ACCESS-KEY"] = self._api_key
            headers["ACCESS-TIMESTAMP"] = ts
            headers["ACCESS-SIGN"] = sign_rest(
                self._api_secret, ts, method, signed_path, body_str
            )

        try:
            resp = await self._client.request(
                method,
                REST_BASE + path,
                params=params,
                content=body_str if json_body is not None else None,
                headers=headers,
            )
        except httpx.HTTPError as e:
            raise TransportError(f"{type(e).__name__}: {e}") from e

        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> Any:
        # Successful order/cancel calls can return an empty body.
        if resp.status_code == 200 and not resp.content:
            return None

        text = resp.text
        try:
            payload = resp.json() if text else None
        except ValueError as e:
            raise TransportError(
                f"non-JSON response (HTTP {resp.status_code}): {text[:200]}"
            ) from e

        # bitFlyer surfaces errors as `{"status": -<n>, "error_message": ..., "data": null}`.
        if isinstance(payload, dict) and "status" in payload and "error_message" in payload:
            status = payload.get("status")
            msg = payload.get("error_message")
            if isinstance(status, int) and status < 0:
                if status in RATE_LIMIT_STATUSES or resp.status_code == 429:
                    raise RateLimitError(status, http_status=resp.status_code, message=msg)
                if status in AUTH_STATUSES:
                    raise AuthError(status, http_status=resp.status_code, message=msg)
                raise ApiError(status, http_status=resp.status_code, message=msg)

        if resp.status_code == 429:
            raise RateLimitError(-132, http_status=429)
        if resp.status_code >= 400:
            raise ApiError(
                resp.status_code, http_status=resp.status_code, message=text[:200]
            )

        return payload
