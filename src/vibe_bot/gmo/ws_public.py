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

PUBLIC_WS_URL = "wss://api.coin.z.com/ws/public/v1"

logger = logging.getLogger(__name__)


class PublicWebSocket:
    """Async public WebSocket client with auto-reconnect.

    Usage:
        async with PublicWebSocket() as ws:
            await ws.subscribe("ticker", "BTC_JPY")
            async for msg in ws.messages():
                print(msg)

    Subscriptions are remembered and re-issued after a reconnect.
    """

    def __init__(
        self,
        url: str = PUBLIC_WS_URL,
        *,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 30.0,
    ) -> None:
        self._url = url
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._conn: ClientConnection | None = None
        self._subscriptions: list[dict[str, Any]] = []
        self._closed = False
        # GMO documents "1 subscribe/unsubscribe per second per IP" — gate sends.
        self._sub_lock = asyncio.Lock()
        self._last_sub_at: float = 0.0
        self._sub_min_interval = 1.05

    async def __aenter__(self) -> "PublicWebSocket":
        await self._connect()
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> None:
        del exc_type, exc, tb
        await self.close()

    async def _connect(self) -> None:
        self._conn = await websockets.connect(self._url, ping_interval=None)
        for sub in self._subscriptions:
            await self._send_throttled(sub)

    async def _send_throttled(self, msg: dict[str, Any]) -> None:
        assert self._conn is not None
        async with self._sub_lock:
            wait = self._last_sub_at + self._sub_min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            await self._conn.send(json.dumps(msg))
            self._last_sub_at = time.monotonic()

    async def close(self) -> None:
        self._closed = True
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def subscribe(
        self, channel: str, symbol: str, *, option: str | None = None
    ) -> None:
        msg: dict[str, Any] = {"command": "subscribe", "channel": channel, "symbol": symbol}
        if option is not None:
            msg["option"] = option
        self._subscriptions.append(msg)
        if self._conn is not None:
            await self._send_throttled(msg)

    async def unsubscribe(self, channel: str, symbol: str) -> None:
        msg = {"command": "unsubscribe", "channel": channel, "symbol": symbol}
        self._subscriptions = [
            s for s in self._subscriptions
            if not (s.get("channel") == channel and s.get("symbol") == symbol)
        ]
        if self._conn is not None:
            await self._send_throttled(msg)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        """Yield decoded JSON messages, transparently reconnecting on drops."""
        delay = self._reconnect_delay
        while not self._closed:
            if self._conn is None:
                try:
                    await self._connect()
                    delay = self._reconnect_delay
                except OSError as e:
                    logger.warning("ws connect failed: %s — retrying in %.1fs", e, delay)
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
                logger.info("ws closed (%s) — reconnecting", e)
                self._conn = None
                continue
            except OSError as e:
                raise TransportError(str(e)) from e

            if isinstance(raw, bytes):
                raw = raw.decode()
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("non-JSON ws frame: %r", raw[:120])
