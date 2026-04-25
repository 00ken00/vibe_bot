"""GMO Coin async client (REST + WebSocket).

Quick start:

    from vibe_bot.gmo import PublicClient, PrivateClient

    async with PublicClient() as pub:
        ticker = await pub.ticker("BTC_JPY")

    # PrivateClient reads GMO_API_KEY / GMO_API_SECRET from env by default.
    async with PrivateClient() as priv:
        margin = await priv.margin()
        order_id = await priv.place_order(
            symbol="BTC_JPY", side="BUY",
            execution_type="LIMIT", size="0.001", price="10000000",
            time_in_force="FAK",
            client_order_id="my-idempotent-id-1",
        )
"""

from .errors import ApiError, AuthError, GMOError, RateLimitError, TransportError
from .http import HttpClient
from .private import PrivateClient
from .public import PublicClient
from .rate_limit import RateLimiter
from .ws_private import (
    CH_EXECUTION_EVENTS,
    CH_ORDER_EVENTS,
    CH_POSITION_EVENTS,
    CH_POSITION_SUMMARY_EVENTS,
    PrivateWebSocket,
)
from .ws_public import PublicWebSocket

__all__ = [
    "ApiError",
    "AuthError",
    "CH_EXECUTION_EVENTS",
    "CH_ORDER_EVENTS",
    "CH_POSITION_EVENTS",
    "CH_POSITION_SUMMARY_EVENTS",
    "GMOError",
    "HttpClient",
    "PrivateClient",
    "PrivateWebSocket",
    "PublicClient",
    "PublicWebSocket",
    "RateLimitError",
    "RateLimiter",
    "TransportError",
]
