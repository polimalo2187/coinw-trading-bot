import logging
from typing import List

from app.exchange.coinw_client import CoinWClient
from app.exchange.base_exchange import Kline

logger = logging.getLogger(__name__)


class MarketScanner:

    def __init__(self, api_key: str = "", api_secret: str = ""):

        # El scanner usa endpoints públicos
        self.exchange = CoinWClient(api_key, api_secret)

    # --------------------------------------------------
    # GET KLINES
    # --------------------------------------------------

    async def get_klines(
        self,
        symbol: str,
        timeframe: str,
        limit: int
    ) -> List[Kline]:

        try:

            klines = await self.exchange.get_klines(
                symbol,
                timeframe,
                limit
            )

            return klines

        except Exception as e:

            logger.exception(f"Kline fetch failed for {symbol}: {e}")

            return []
