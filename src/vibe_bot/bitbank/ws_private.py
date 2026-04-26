from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .errors import TransportError
from .private import PrivateClient

# bitbank publishes private events via PubNub, not over a native WebSocket.
PUBNUB_SUB_KEY = "sub-c-ecebae8e-dd60-11e6-b6b1-02ee2ddab7fe"
PUBNUB_BASE = "https://ps.pndsn.com"

# Token TTL is documented as ~12 hours; refresh a bit ahead of that.
DEFAULT_TOKEN_REFRESH_SECONDS = 11 * 60 * 60

logger = logging.getLogger(__name__)


class PrivateWebSocket:
    """Async private stream client for bitbank.

    bitbank's private real-time feed is delivered over PubNub (a third-party
    long-poll message bus), not a native WebSocket — we keep the GMO-style
    `PrivateWebSocket` name so callers see a consistent surface.

    The flow:
      1. `PrivateClient.subscribe()` mints a `pubnub_channel` + `pubnub_token`
      2. We long-poll PubNub's v2 subscribe endpoint with that token
      3. Tokens last ~12h; we refresh ahead of expiry by re-calling subscribe()

    Usage:
        async with PrivateWebSocket() as ws:
            async for msg in ws.messages():
                method = msg.get("method")
                params = msg.get("params", [])
                ...
    """

    def __init__(
        self,
        private_client: PrivateClient | None = None,
        *,
        sub_key: str = PUBNUB_SUB_KEY,
        base_url: str = PUBNUB_BASE,
        token_refresh_seconds: float = DEFAULT_TOKEN_REFRESH_SECONDS,
        request_timeout: float = 310.0,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 30.0,
        uuid: str = "vibe-bot",
    ) -> None:
        self._client = private_client or PrivateClient()
        self._owns_client = private_client is None
        self._sub_key = sub_key
        self._base_url = base_url
        self._token_refresh_seconds = token_refresh_seconds
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._uuid = uuid

        # PubNub long-poll holds open up to ~280s; pad for jitter.
        self._http = httpx.AsyncClient(timeout=request_timeout)

        self._channel: str | None = None
        self._token: str | None = None
        self._timetoken: str = "0"
        self._region: str | None = None
        self._closed = False
        self._refresh_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "PrivateWebSocket":
        await self._mint_token()
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> None:
        del exc_type, exc, tb
        await self.close()

    async def close(self) -> None:
        self._closed = True
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except (asyncio.CancelledError, Exception):
                pass
            self._refresh_task = None
        await self._http.aclose()
        if self._owns_client:
            await self._client.aclose()

    async def _mint_token(self) -> None:
        sub = await self._client.subscribe()
        self._channel = sub.pubnub_channel
        self._token = sub.pubnub_token

    async def _refresh_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(self._token_refresh_seconds)
                if not self._closed:
                    await self._mint_token()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("pubnub token refresh failed: %s — will retry on next loop", e)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        """Yield each PubNub message payload (the bitbank `message` JSON object).

        Reconnects with exponential backoff on transient transport errors and
        re-mints the token when PubNub returns 403 (token expired/revoked).
        """
        delay = self._reconnect_delay
        while not self._closed:
            if self._channel is None or self._token is None:
                try:
                    await self._mint_token()
                except Exception as e:
                    logger.warning("subscribe mint failed: %s", e)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self._max_reconnect_delay)
                    continue

            assert self._channel is not None
            url = f"{self._base_url}/v2/subscribe/{self._sub_key}/{self._channel}/0"
            params: dict[str, Any] = {
                "tt": self._timetoken,
                "uuid": self._uuid,
                "auth": self._token,
            }
            if self._region is not None:
                params["tr"] = self._region

            try:
                resp = await self._http.get(url, params=params)
            except httpx.HTTPError as e:
                logger.info("pubnub long-poll error: %s — reconnecting", e)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._max_reconnect_delay)
                continue

            if resp.status_code == 403:
                # Token expired or revoked: re-mint and retry.
                logger.info("pubnub returned 403 — re-minting subscribe token")
                self._token = None
                continue
            if resp.status_code != 200:
                logger.warning(
                    "pubnub HTTP %s: %s", resp.status_code, resp.text[:200]
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._max_reconnect_delay)
                continue

            try:
                envelope = resp.json()
            except ValueError as e:
                raise TransportError(f"non-JSON pubnub response: {resp.text[:200]}") from e

            timetoken = envelope.get("t") or {}
            self._timetoken = timetoken.get("t") or self._timetoken
            self._region = timetoken.get("r") or self._region
            delay = self._reconnect_delay

            for entry in envelope.get("m") or []:
                payload = entry.get("d")
                if isinstance(payload, dict):
                    yield payload
