from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from .errors import TransportError

PUBLIC_WS_URL = "wss://ws-api.coincheck.com"

logger = logging.getLogger(__name__)


class PublicWebSocket:
    """Async public stream client for Coincheck.

    Channels are `<pair>-trades` and `<pair>-orderbook`, e.g.
    `btc_jpy-trades`. Subscriptions are remembered and re-issued after a
    reconnect.
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
        self._channels: list[str] = []
        self._closed = False

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

    async def close(self) -> None:
        self._closed = True
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _connect(self) -> None:
        self._conn = await websockets.connect(self._url)
        for channel in self._channels:
            await self._send_subscribe(channel)

    async def _send(self, payload: dict[str, Any]) -> None:
        assert self._conn is not None
        await self._conn.send(json.dumps(payload))

    async def _send_subscribe(self, channel: str) -> None:
        await self._send({"type": "subscribe", "channel": channel})

    async def _send_unsubscribe(self, channel: str) -> None:
        await self._send({"type": "unsubscribe", "channel": channel})

    async def subscribe(self, channel: str) -> None:
        if channel not in self._channels:
            self._channels.append(channel)
        if self._conn is not None:
            await self._send_subscribe(channel)

    async def unsubscribe(self, channel: str) -> None:
        self._channels = [c for c in self._channels if c != channel]
        if self._conn is not None:
            await self._send_unsubscribe(channel)

    async def messages(self) -> AsyncIterator[Any]:
        delay = self._reconnect_delay
        while not self._closed:
            if self._conn is None:
                try:
                    await self._connect()
                    delay = self._reconnect_delay
                except OSError as e:
                    logger.warning("ws connect failed: %s; retrying in %.1fs", e, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self._max_reconnect_delay)
                    continue

            conn = self._conn
            assert conn is not None
            try:
                raw = await conn.recv()
            except websockets.ConnectionClosed:
                if self._closed:
                    return
                self._conn = None
                continue
            except OSError as e:
                raise TransportError(str(e)) from e

            text = raw.decode() if isinstance(raw, bytes) else raw
            if not text:
                continue
            try:
                yield json.loads(text)
            except json.JSONDecodeError:
                logger.warning("non-JSON ws frame: %r", text[:120])
