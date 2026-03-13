import asyncio
import logging

from app.log_config import setup_logging
from app.database import db
from app.bot import TradingBot
from app.trading_loop import TradingLoop

logger = logging.getLogger(__name__)


async def start_trading_for_users():

    users = list(db.users.find({"status": "active"}))

    loops = []

    for user in users:

        loop = TradingLoop(user)

        loops.append(asyncio.create_task(loop.run()))

        logger.info(f"Trading loop started for user {user['telegram_id']}")

    if loops:
        await asyncio.gather(*loops)


async def main():

    setup_logging()

    logger.info("Starting CoinW Trading Bot")

    bot = TradingBot()

    asyncio.create_task(bot.run())

    await start_trading_for_users()


if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        logger.info("Bot stopped")
