from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from .errors import TransportError
from .private import PrivateClient

# Token is appended to the path: wss://api.coin.z.com/ws/private/v1/{token}
PRIVATE_WS_BASE = "wss://api.coin.z.com/ws/private/v1"

# Channel name constants per GMO spec.
CH_EXECUTION_EVENTS = "executionEvents"
CH_ORDER_EVENTS = "orderEvents"
CH_POSITION_EVENTS = "positionEvents"
CH_POSITION_SUMMARY_EVENTS = "positionSummaryEvents"

logger = logging.getLogger(__name__)


class PrivateWebSocket:
    """Async private WebSocket client. Fetches access-token via REST, auto-extends.

    The PrivateClient passed in (or auto-created) is used to mint and extend the
    WS access token. Token is extended every 30min by default since GMO tokens
    expire after ~60min.
    """

    def __init__(
        self,
        private_client: PrivateClient | None = None,
        *,
        url_base: str = PRIVATE_WS_BASE,
        token_extend_interval: float = 30 * 60,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 30.0,
    ) -> None:
        self._client = private_client or PrivateClient()
        self._owns_client = private_client is None
        self._url_base = url_base
        self._token_extend_interval = token_extend_interval
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay

        self._token: str | None = None
        self._conn: ClientConnection | None = None
        self._subscriptions: list[str] = []
        self._closed = False
        self._extend_task: asyncio.Task[None] | None = None

        self._sub_lock = asyncio.Lock()
        self._last_sub_at: float = 0.0
        self._sub_min_interval = 1.05

    async def __aenter__(self) -> "PrivateWebSocket":
        await self._connect()
        self._extend_task = asyncio.create_task(self._extend_loop())
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
        if self._extend_task is not None:
            self._extend_task.cancel()
            try:
                await self._extend_task
            except (asyncio.CancelledError, Exception):
                pass
            self._extend_task = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        if self._token is not None:
            try:
                await self._client.delete_ws_token(self._token)
            except Exception as e:
                logger.warning("failed to delete ws token: %s", e)
            self._token = None
        if self._owns_client:
            await self._client.aclose()

    async def _connect(self) -> None:
        if self._token is None:
            self._token = await self._client.create_ws_token()
        url = f"{self._url_base}/{self._token}"
        self._conn = await websockets.connect(url, ping_interval=None)
        for channel in self._subscriptions:
            await self._send_subscribe(channel)

    async def _send_subscribe(self, channel: str) -> None:
        assert self._conn is not None
        msg = {"command": "subscribe", "channel": channel}
        async with self._sub_lock:
            wait = self._last_sub_at + self._sub_min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            await self._conn.send(json.dumps(msg))
            self._last_sub_at = time.monotonic()

    async def subscribe(self, channel: str) -> None:
        if channel not in self._subscriptions:
            self._subscriptions.append(channel)
        if self._conn is not None:
            await self._send_subscribe(channel)

    async def _extend_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(self._token_extend_interval)
                if self._token is not None and not self._closed:
                    await self._client.extend_ws_token(self._token)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("ws token extend failed: %s — will fetch new on reconnect", e)
                self._token = None

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        delay = self._reconnect_delay
        while not self._closed:
            if self._conn is None:
                try:
                    await self._connect()
                    delay = self._reconnect_delay
                except OSError as e:
                    logger.warning("private ws connect failed: %s", e)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self._max_reconnect_delay)
                    continue

            conn = self._conn
            assert conn is not None
            try:
                raw = await conn.recv()
            except websockets.ConnectionClosed as e:
                if self._closed:
                    return
                logger.info("private ws closed (%s) — reconnecting", e)
                self._conn = None
                continue
            except OSError as e:
                raise TransportError(str(e)) from e

            if isinstance(raw, bytes):
                raw = raw.decode()
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("non-JSON private ws frame: %r", raw[:120])
