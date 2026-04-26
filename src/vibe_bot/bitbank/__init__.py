"""bitbank async client (REST + Socket.IO public stream + PubNub private stream).

Quick start:

    from vibe_bot.bitbank import PublicClient, PrivateClient

    async with PublicClient() as pub:
        ticker = await pub.ticker("btc_jpy")

    # PrivateClient reads BITBANK_API_KEY / BITBANK_API_SECRET from env by default.
    async with PrivateClient() as priv:
        assets = await priv.assets()
        order = await priv.place_order(
            pair="btc_jpy", side="buy",
            order_type="limit", amount="0.0001", price="10000000",
            post_only=True,
        )
"""

from .errors import ApiError, AuthError, BitbankError, RateLimitError, TransportError
from .http import HttpClient
from .private import PrivateClient
from .public import PublicClient
from .rate_limit import RateLimiter
from .ws_private import PrivateWebSocket
from .ws_public import PublicWebSocket

__all__ = [
    "ApiError",
    "AuthError",
    "BitbankError",
    "HttpClient",
    "PrivateClient",
    "PrivateWebSocket",
    "PublicClient",
    "PublicWebSocket",
    "RateLimitError",
    "RateLimiter",
    "TransportError",
]
