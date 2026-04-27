"""bitFlyer Lightning async client (REST + JSON-RPC over WebSocket realtime).

Quick start:

    from vibe_bot.bitflyer import PublicClient, PrivateClient

    async with PublicClient() as pub:
        ticker = await pub.ticker("BTC_JPY")

    # PrivateClient reads BITFLYER_API_KEY / BITFLYER_API_SECRET from env by default.
    async with PrivateClient() as priv:
        balance = await priv.balance()
        ack = await priv.send_child_order(
            product_code="BTC_JPY",
            child_order_type="LIMIT",
            side="BUY",
            size="0.001",
            price="10000000",
            time_in_force="GTC",
        )
"""

from .errors import (
    ApiError,
    AuthError,
    BitflyerError,
    RateLimitError,
    TransportError,
)
from .http import HttpClient
from .private import PrivateClient
from .public import PublicClient
from .rate_limit import RateLimiter
from .ws_private import PrivateWebSocket
from .ws_public import PublicWebSocket

__all__ = [
    "ApiError",
    "AuthError",
    "BitflyerError",
    "HttpClient",
    "PrivateClient",
    "PrivateWebSocket",
    "PublicClient",
    "PublicWebSocket",
    "RateLimitError",
    "RateLimiter",
    "TransportError",
]
