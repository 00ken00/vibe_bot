from __future__ import annotations

import asyncio
import itertools
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from .errors import TransportError

PUBLIC_WS_URL = "wss://ws.lightstream.bitflyer.com/json-rpc"

logger = logging.getLogger(__name__)


class PublicWebSocket:
    """Async public stream client for bitFlyer.

    bitFlyer's realtime feed is JSON-RPC 2.0 over a single WebSocket. We send
    `{"jsonrpc":"2.0","method":"subscribe","params":{"channel":"..."}}` and
    receive `{"jsonrpc":"2.0","method":"channelMessage","params":{"channel":...,
    "message":...}}` frames.

    Channel naming:
      - `lightning_ticker_<product_code>`           e.g. `lightning_ticker_BTC_JPY`
      - `lightning_executions_<product_code>`
      - `lightning_board_snapshot_<product_code>`   (full snapshot, throttled)
      - `lightning_board_<product_code>`            (diff updates)

    Subscriptions are remembered and re-issued after a reconnect.

    Usage:
        async with PublicWebSocket() as ws:
            await ws.subscribe("lightning_ticker_BTC_JPY")
            async for msg in ws.messages():
                print(msg["channel"], msg["message"])
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
        self._id_counter = itertools.count(1)

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
        # bitFlyer uses standard WebSocket pings; let websockets manage them.
        self._conn = await websockets.connect(self._url)
        for channel in self._channels:
            await self._send_subscribe(channel)

    async def _send(self, payload: dict[str, Any]) -> None:
        assert self._conn is not None
        await self._conn.send(json.dumps(payload))

    async def _send_subscribe(self, channel: str) -> None:
        await self._send({
            "jsonrpc": "2.0",
            "method": "subscribe",
            "params": {"channel": channel},
            "id": next(self._id_counter),
        })

    async def _send_unsubscribe(self, channel: str) -> None:
        await self._send({
            "jsonrpc": "2.0",
            "method": "unsubscribe",
            "params": {"channel": channel},
            "id": next(self._id_counter),
        })

    async def subscribe(self, channel: str) -> None:
        """Subscribe to a public channel. See class docstring for naming."""
        if channel not in self._channels:
            self._channels.append(channel)
        if self._conn is not None:
            await self._send_subscribe(channel)

    async def unsubscribe(self, channel: str) -> None:
        self._channels = [c for c in self._channels if c != channel]
        if self._conn is not None:
            await self._send_unsubscribe(channel)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        """Yield decoded `channelMessage` payloads, transparently reconnecting.

        Each yielded dict is the inner `params` object from a JSON-RPC
        `channelMessage` notification: `{"channel": "...", "message": ...}`.
        Subscribe acks (responses with an `id`) are silently consumed.
        """
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

            text = raw.decode() if isinstance(raw, bytes) else raw
            if not text:
                continue
            try:
                frame = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("non-JSON ws frame: %r", text[:120])
                continue

            if not isinstance(frame, dict):
                continue
            if frame.get("method") == "channelMessage":
                params = frame.get("params")
                if isinstance(params, dict):
                    yield params
