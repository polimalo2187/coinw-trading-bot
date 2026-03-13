import logging
import time
from typing import Any

from app.config import config
from app.exchange.coinw_client import CoinWClient

logger = logging.getLogger(__name__)


class MarketDataService:
    """
    Shared market data service.

    Responsibilities:
    - fetch klines from CoinW public endpoints
    - cache recent responses for a short period
    - reduce duplicated API calls across users
    """

    def __init__(self) -> None:
        # Public endpoints do not require real credentials
        self.exchange = CoinWClient(api_key="", api_secret="")
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_ttl_seconds = 3

    async def get_klines(self, symbol: str, timeframe: str | None = None, limit: int | None = None):
        timeframe = timeframe or config.timeframe
        limit = limit or config.candle_limit

        cache_key = f"{symbol}:{timeframe}:{limit}"
        now = time.time()

        cached = self._cache.get(cache_key)
        if cached:
            age = now - cached["ts"]
            if age <= self._cache_ttl_seconds:
                return cached["data"]

        klines = await self.exchange.get_klines(
            symbol=symbol,
            interval=timeframe,
            limit=limit,
        )

        self._cache[cache_key] = {
            "ts": now,
            "data": klines,
        }

        return klines

    def clear_cache(self) -> None:
        self._cache.clear()
        logger.info("Market data cache cleared")
