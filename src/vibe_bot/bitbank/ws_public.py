from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from .errors import TransportError

PUBLIC_WS_URL = "wss://stream.bitbank.cc/socket.io/?EIO=4&transport=websocket"

# Engine.IO v4 packet type prefixes (single character at the start of a frame).
EIO_OPEN = "0"
EIO_CLOSE = "1"
EIO_PING = "2"
EIO_PONG = "3"
EIO_MESSAGE = "4"

# Socket.IO v5 packet type prefixes (follow EIO_MESSAGE).
SIO_CONNECT = "0"
SIO_DISCONNECT = "1"
SIO_EVENT = "2"
SIO_ACK = "3"
SIO_CONNECT_ERROR = "4"

logger = logging.getLogger(__name__)


class PublicWebSocket:
    """Async public stream client for bitbank.

    bitbank's public stream rides Socket.IO 4 (Engine.IO 4) over a single WS
    connection — not raw JSON-over-WS like GMO. We speak just enough of the
    protocol inline to avoid pulling in `python-socketio`.

    Usage:
        async with PublicWebSocket() as ws:
            await ws.subscribe("ticker_btc_jpy")
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
        self._rooms: list[str] = []
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
            try:
                await self._conn.send(EIO_MESSAGE + SIO_DISCONNECT)
            except Exception:
                pass
            await self._conn.close()
            self._conn = None

    async def _connect(self) -> None:
        # Disable websockets' built-in ping; Engine.IO has its own ping/pong.
        conn = await websockets.connect(self._url, ping_interval=None)
        # Server greets us with `0{json}` (Engine.IO OPEN).
        first = await conn.recv()
        first = first.decode() if isinstance(first, bytes) else first
        if not first.startswith(EIO_OPEN):
            await conn.close()
            raise TransportError(f"unexpected EIO greeting: {first[:80]!r}")

        # Connect the default Socket.IO namespace.
        await conn.send(EIO_MESSAGE + SIO_CONNECT)
        ack = await conn.recv()
        ack = ack.decode() if isinstance(ack, bytes) else ack
        if not ack.startswith(EIO_MESSAGE + SIO_CONNECT):
            await conn.close()
            raise TransportError(f"unexpected SIO connect ack: {ack[:80]!r}")

        self._conn = conn
        for room in self._rooms:
            await self._send_join(room)

    async def _send_join(self, room: str) -> None:
        assert self._conn is not None
        frame = EIO_MESSAGE + SIO_EVENT + json.dumps(["join-room", room])
        await self._conn.send(frame)

    async def _send_leave(self, room: str) -> None:
        assert self._conn is not None
        frame = EIO_MESSAGE + SIO_EVENT + json.dumps(["leave-room", room])
        await self._conn.send(frame)

    async def subscribe(self, room: str) -> None:
        """Subscribe to a room. Channel names follow the `<channel>_<pair>`
        convention, e.g. `ticker_btc_jpy`, `transactions_xrp_jpy`,
        `depth_diff_btc_jpy`, `depth_whole_btc_jpy`, `circuit_break_info_btc_jpy`.
        """
        if room not in self._rooms:
            self._rooms.append(room)
        if self._conn is not None:
            await self._send_join(room)

    async def unsubscribe(self, room: str) -> None:
        self._rooms = [r for r in self._rooms if r != room]
        if self._conn is not None:
            await self._send_leave(room)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        """Yield decoded room messages, transparently reconnecting on drops.

        Each yielded dict is the inner `message` payload from a `42["message", ...]`
        frame, augmented with `room_name` so callers can route by channel.
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

            head = text[0]
            if head == EIO_PING:
                # Engine.IO heartbeat; respond and keep going.
                try:
                    await conn.send(EIO_PONG)
                except websockets.ConnectionClosed:
                    self._conn = None
                continue
            if head == EIO_CLOSE:
                self._conn = None
                continue
            if head != EIO_MESSAGE or len(text) < 2:
                # Unknown / non-message Engine.IO packet — ignore.
                continue

            sio_type = text[1]
            body = text[2:]
            if sio_type != SIO_EVENT or not body:
                # Connect / disconnect / ack frames don't carry user payloads.
                continue

            try:
                event = json.loads(body)
            except json.JSONDecodeError:
                logger.warning("non-JSON SIO event: %r", body[:120])
                continue

            # Server emits ["message", {"room_name": "...", "message": {...}}]
            if isinstance(event, list) and len(event) >= 2 and event[0] == "message":
                payload = event[1]
                if isinstance(payload, dict):
                    yield payload
