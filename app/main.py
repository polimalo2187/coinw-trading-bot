import asyncio
import logging

from app.config import config
from app.database import db
from app.user_manager import user_manager
from app.trading_engine import TradingEngine
from app.market_data import MarketDataService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("main")


class BotRunner:
    """
    Main runtime controller.

    Responsibilities:
    - start services
    - load active users
    - run trading loops
    """

    def __init__(self):
        self.market = MarketDataService()
        self.engines = {}

    async def start(self):

        logger.info("Starting NeoTrade Bot")

        await db.connect()

        users = user_manager.list_active_users()

        logger.info("Active users loaded: %s", len(users))

        for user in users:
            engine = TradingEngine(user)
            self.engines[user["_id"]] = engine

        await self.run_loop()

    async def run_loop(self):

        symbols = config.trade_symbols

        while True:

            try:

                for symbol in symbols:

                    klines = await self.market.get_klines(symbol)

                    for engine in self.engines.values():
                        await engine.process_symbol(symbol, klines)

                await asyncio.sleep(config.loop_interval_seconds)

            except Exception:
                logger.exception("Main loop failure")
                await asyncio.sleep(5)


async def main():

    runner = BotRunner()

    await runner.start()


if __name__ == "__main__":

    asyncio.run(main())
