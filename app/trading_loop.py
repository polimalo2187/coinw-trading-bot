import asyncio
import logging

from app.config import config
from app.market_scanner import MarketScanner
from app.trading_engine import TradingEngine

logger = logging.getLogger(__name__)


class TradingLoop:

    def __init__(self, user):

        self.user = user

        self.engine = TradingEngine(user)

        self.scanner = MarketScanner()

        self.running = True

    # --------------------------------------------------
    # MAIN LOOP
    # --------------------------------------------------

    async def run(self):

        logger.info(f"Starting trading loop for user {self.user['telegram_id']}")

        while self.running:

            try:

                await self.scan_market()

            except Exception as e:

                logger.exception(f"Trading loop error: {e}")

            await asyncio.sleep(config.scan_interval_seconds)

    # --------------------------------------------------
    # MARKET SCAN
    # --------------------------------------------------

    async def scan_market(self):

        symbols = config.symbols

        for symbol in symbols:

            try:

                klines = await self.scanner.get_klines(
                    symbol,
                    config.timeframe,
                    config.candle_limit
                )

                await self.engine.process_symbol(symbol, klines)

            except Exception as e:

                logger.exception(f"Error processing {symbol}: {e}")

    # --------------------------------------------------
    # STOP LOOP
    # --------------------------------------------------

    def stop(self):

        logger.info("Stopping trading loop")

        self.running = False
