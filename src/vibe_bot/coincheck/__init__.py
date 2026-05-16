"""Coincheck async client (REST + WebSocket).

Quick start:

    from vibe_bot.coincheck import PublicClient, PrivateClient

    async with PublicClient() as pub:
        ticker = await pub.ticker("btc_jpy")

    # PrivateClient reads COINCHECK_API_KEY / COINCHECK_API_SECRET from env.
    async with PrivateClient() as priv:
        balance = await priv.balance()
"""

from .errors import ApiError, AuthError, CoincheckError, RateLimitError, TransportError
from .http import HttpClient
from .private import PrivateClient
from .public import PublicClient
from .rate_limit import RateLimiter
from .ws_private import CH_EXECUTION_EVENTS, CH_ORDER_EVENTS, PrivateWebSocket
from .ws_public import PublicWebSocket

__all__ = [
    "ApiError",
    "AuthError",
    "CH_EXECUTION_EVENTS",
    "CH_ORDER_EVENTS",
    "CoincheckError",
    "HttpClient",
    "PrivateClient",
    "PrivateWebSocket",
    "PublicClient",
    "PublicWebSocket",
    "RateLimitError",
    "RateLimiter",
    "TransportError",
]
