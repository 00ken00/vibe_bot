from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from .auth import now_nonce_ms, sign
from .errors import AuthError, TransportError

PRIVATE_WS_URL = "wss://stream.coincheck.com"
PRIVATE_WS_SIGN_URL = "wss://stream.coincheck.com/private"
CH_ORDER_EVENTS = "order-events"
CH_EXECUTION_EVENTS = "execution-events"

logger = logging.getLogger(__name__)


class PrivateWebSocket:
    """Async private stream client for Coincheck order and execution events."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        url: str = PRIVATE_WS_URL,
        *,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 30.0,
    ) -> None:
        key = api_key or os.environ.get("COINCHECK_API_KEY")
        secret = api_secret or os.environ.get("COINCHECK_API_SECRET")
        if not key or not secret:
            raise AuthError(
                -1,
                message="COINCHECK_API_KEY / COINCHECK_API_SECRET not set",
            )
        self._api_key = key
        self._api_secret = secret
        self._url = url
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._conn: ClientConnection | None = None
        self._channels: list[str] = []
        self._closed = False

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
        if self._channels:
            await self._send_subscribe(self._channels)

    async def _send(self, payload: dict[str, Any]) -> None:
        assert self._conn is not None
        await self._conn.send(json.dumps(payload))

    async def _authenticate(self) -> None:
        nonce = now_nonce_ms()
        signature = sign(self._api_secret, nonce, PRIVATE_WS_SIGN_URL)
        await self._send({
            "type": "login",
            "access_key": self._api_key,
            "access_nonce": nonce,
            "access_signature": signature,
        })
        assert self._conn is not None
        raw = await self._conn.recv()
        text = raw.decode() if isinstance(raw, bytes) else raw
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            raise AuthError(-1, message=f"non-JSON auth response: {text[:120]}") from e
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise AuthError(-1, message=str(payload)[:200])

    async def _send_subscribe(self, channels: list[str]) -> None:
        await self._send({"type": "subscribe", "channels": channels})

    async def _send_unsubscribe(self, channels: list[str]) -> None:
        await self._send({"type": "unsubscribe", "channels": channels})

    async def subscribe(self, *channels: str) -> None:
        new_channels = [c for c in channels if c not in self._channels]
        self._channels.extend(new_channels)
        if self._conn is not None and new_channels:
            await self._send_subscribe(new_channels)

    async def unsubscribe(self, *channels: str) -> None:
        remove = set(channels)
        self._channels = [c for c in self._channels if c not in remove]
        if self._conn is not None and channels:
            await self._send_unsubscribe(list(channels))

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
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
                except AuthError:
                    raise

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
                payload = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("non-JSON ws frame: %r", text[:120])
                continue
            if isinstance(payload, dict):
                yield payload
