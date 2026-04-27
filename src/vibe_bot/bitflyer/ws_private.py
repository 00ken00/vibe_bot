from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from .auth import fresh_nonce, now_timestamp_ms, sign_realtime
from .errors import AuthError, TransportError

PRIVATE_WS_URL = "wss://ws.lightstream.bitflyer.com/json-rpc"

logger = logging.getLogger(__name__)


class PrivateWebSocket:
    """Async private stream client for bitFlyer.

    Uses the same JSON-RPC over WebSocket transport as `PublicWebSocket`, but
    issues an `auth` call right after connecting and only subscribes to
    private channels (typically `child_order_events` and `parent_order_events`).

    Auth payload (per docs):
      method = "auth"
      params = {api_key, timestamp (ms), nonce (16-255 chars), signature}
      signature = HMAC-SHA256(secret, timestamp + nonce) hex

    Reads `BITFLYER_API_KEY` / `BITFLYER_API_SECRET` from the environment by
    default. Auth is re-issued automatically on reconnect.

    Usage:
        async with PrivateWebSocket() as ws:
            await ws.subscribe("child_order_events")
            async for msg in ws.messages():
                ...
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        url: str = PRIVATE_WS_URL,
        *,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 30.0,
    ) -> None:
        key = api_key or os.environ.get("BITFLYER_API_KEY")
        secret = api_secret or os.environ.get("BITFLYER_API_SECRET")
        if not key or not secret:
            raise AuthError(
                -201,
                message="BITFLYER_API_KEY / BITFLYER_API_SECRET not set",
            )
        self._api_key = key
        self._api_secret = secret
        self._url = url
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._conn: ClientConnection | None = None
        self._channels: list[str] = []
        self._closed = False
        self._id_counter = itertools.count(1)

    async def __aenter__(self) -> "PrivateWebSocket":
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
        await self._authenticate()
        for channel in self._channels:
            await self._send_subscribe(channel)

    async def _send(self, payload: dict[str, Any]) -> None:
        assert self._conn is not None
        await self._conn.send(json.dumps(payload))

    async def _authenticate(self) -> None:
        ts = now_timestamp_ms()
        nonce = fresh_nonce()
        signature = sign_realtime(self._api_secret, ts, nonce)
        auth_id = next(self._id_counter)
        await self._send({
            "jsonrpc": "2.0",
            "method": "auth",
            "params": {
                "api_key": self._api_key,
                "timestamp": int(ts),
                "nonce": nonce,
                "signature": signature,
            },
            "id": auth_id,
        })
        # Wait for the auth response before subscribing.
        assert self._conn is not None
        while True:
            raw = await self._conn.recv()
            text = raw.decode() if isinstance(raw, bytes) else raw
            try:
                frame = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(frame, dict):
                continue
            # Skip any unrelated channelMessage frames that arrived first.
            if frame.get("id") != auth_id:
                continue
            if frame.get("error") or frame.get("result") is False:
                err = frame.get("error") or {"message": "auth rejected"}
                raise AuthError(
                    -200,
                    message=str(err.get("message") if isinstance(err, dict) else err),
                )
            return

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
        """Subscribe to a private channel: typically `child_order_events` or
        `parent_order_events`."""
        if channel not in self._channels:
            self._channels.append(channel)
        if self._conn is not None:
            await self._send_subscribe(channel)

    async def unsubscribe(self, channel: str) -> None:
        self._channels = [c for c in self._channels if c != channel]
        if self._conn is not None:
            await self._send_unsubscribe(channel)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        """Yield decoded `channelMessage` payloads, transparently reconnecting
        and re-authenticating after drops."""
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
                except AuthError:
                    raise

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
