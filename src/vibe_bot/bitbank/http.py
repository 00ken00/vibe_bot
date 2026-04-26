from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import httpx

from .auth import now_timestamp_ms, sign_nonce, sign_time_window
from .errors import (
    AUTH_CODES,
    RATE_LIMIT_CODES,
    ApiError,
    AuthError,
    RateLimitError,
    TransportError,
)
from .rate_limit import RateLimiter

PUBLIC_BASE = "https://public.bitbank.cc"
PRIVATE_BASE = "https://api.bitbank.cc"
PRIVATE_PATH_PREFIX = "/v1"

DEFAULT_TIME_WINDOW_MS = "5000"


class HttpClient:
    """Async HTTP transport for bitbank REST. Handles signing, rate limiting, error mapping.

    Auth modes:
      - "time_window" (default): uses ACCESS-REQUEST-TIME + ACCESS-TIME-WINDOW
        headers. This is bitbank's recommended modern method and is more robust
        across clock skew than a strict monotonic nonce.
      - "nonce": uses ACCESS-NONCE (a millisecond timestamp that must increase
        per request). Use if your account is locked to nonce-only auth.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        rate_limiter: RateLimiter | None = None,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
        auth_mode: str = "time_window",
        time_window_ms: str = DEFAULT_TIME_WINDOW_MS,
    ) -> None:
        if auth_mode not in ("time_window", "nonce"):
            raise ValueError(f"auth_mode must be 'time_window' or 'nonce', got {auth_mode!r}")
        self._api_key = api_key
        self._api_secret = api_secret
        self._limiter = rate_limiter or RateLimiter()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._auth_mode = auth_mode
        self._time_window_ms = time_window_ms

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
        base: str | None = None,
    ) -> Any:
        """Public REST. `path` should start with `/`. `base` overrides the default
        public host — pass `PRIVATE_BASE` for unsigned endpoints that live on the
        private host (e.g. `/v1/spot/pairs`, `/v1/spot/status`)."""
        return await self._request(
            method,
            (base or PUBLIC_BASE) + path,
            sign_target=None,
            params=params,
            signed=False,
        )

    async def private(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> Any:
        """Private REST. `path` is the bare resource path (e.g. '/user/assets');
        the `/v1` prefix is added here AND included in the signature payload."""
        full_path = PRIVATE_PATH_PREFIX + path
        return await self._request(
            method,
            PRIVATE_BASE + full_path,
            sign_target=full_path,
            params=params,
            json_body=json_body,
            signed=True,
        )

    async def _request(
        self,
        method: str,
        url: str,
        sign_target: str | None,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        signed: bool,
    ) -> Any:
        await self._limiter.acquire()

        headers: dict[str, str] = {}
        body_str = ""
        if json_body is not None:
            body_str = json.dumps(json_body, separators=(",", ":"))
            headers["Content-Type"] = "application/json"

        if signed:
            if not self._api_key or not self._api_secret:
                raise AuthError(20003, message="missing api key/secret")
            assert sign_target is not None
            if method.upper() == "GET":
                qs = urlencode(params, doseq=True) if params else ""
                payload = f"{sign_target}?{qs}" if qs else sign_target
            else:
                payload = body_str

            headers["ACCESS-KEY"] = self._api_key
            if self._auth_mode == "time_window":
                req_time = now_timestamp_ms()
                headers["ACCESS-REQUEST-TIME"] = req_time
                headers["ACCESS-TIME-WINDOW"] = self._time_window_ms
                headers["ACCESS-SIGNATURE"] = sign_time_window(
                    self._api_secret, req_time, self._time_window_ms, payload
                )
            else:
                nonce = now_timestamp_ms()
                headers["ACCESS-NONCE"] = nonce
                headers["ACCESS-SIGNATURE"] = sign_nonce(self._api_secret, nonce, payload)

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
            raise TransportError(
                f"non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}"
            ) from e

        success = envelope.get("success")
        data = envelope.get("data")

        if success == 1:
            return data

        if resp.status_code == 429:
            code = (data or {}).get("code") if isinstance(data, dict) else None
            raise RateLimitError(code or -1, http_status=resp.status_code)

        # success == 0 (or absent): bitbank packs the code under data.code
        code = None
        if isinstance(data, dict):
            code = data.get("code")
        if code is None:
            raise ApiError(-1, http_status=resp.status_code, message=str(envelope)[:200])
        if code in RATE_LIMIT_CODES:
            raise RateLimitError(code, http_status=resp.status_code)
        if code in AUTH_CODES:
            raise AuthError(code, http_status=resp.status_code)
        raise ApiError(code, http_status=resp.status_code)
