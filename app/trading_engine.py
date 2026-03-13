import logging
from decimal import Decimal
from typing import Optional

from app.database import db
from app.risk import RiskManager
from app.strategy import Strategy
from app.exchange.coinw_client import CoinWClient
from app.exchange.base_exchange import OrderRequest
from app.config import config

logger = logging.getLogger(__name__)


class TradingEngine:

    def __init__(self, user):

        self.user = user

        self.exchange = CoinWClient(
            user["api_key"],
            user["api_secret"]
        )

        self.strategy = Strategy()
        self.risk = RiskManager()

    # --------------------------------------------------
    # MAIN
    # --------------------------------------------------

    async def process_symbol(self, symbol: str, klines):

        signal = self.strategy.generate_signal(symbol, klines)

        if not signal:
            return

        if await self.exchange.has_open_position(symbol):
            logger.info(f"{symbol} position already open")
            return

        await self.open_trade(symbol, signal)

    # --------------------------------------------------
    # OPEN TRADE
    # --------------------------------------------------

    async def open_trade(self, symbol: str, signal: dict):

        logger.info(f"Opening trade {symbol}")

        price = await self.exchange.get_price(symbol)

        balance = await self.exchange.get_balance(config.default_quote_asset)

        size = self.risk.calculate_position_size(
            balance.free,
            price
        )

        side = "BUY" if signal["side"] == "LONG" else "SELL"

        order = OrderRequest(
            symbol=symbol,
            side=side,
            position_side=signal["side"],
            quantity=Decimal(size),
            order_type="MARKET"
        )

        result = await self.exchange.open_position(order)

        if not result.success:

            logger.error("Order failed")

            return

        entry_price = price

        logger.info(f"Trade opened {symbol} at {entry_price}")

        await self.set_protection(symbol, signal, entry_price, size)

        db.create_position({
            "user_id": self.user["_id"],
            "symbol": symbol,
            "side": signal["side"],
            "size": size,
            "entry_price": float(entry_price),
            "status": "open"
        })

    # --------------------------------------------------
    # SL / TP
    # --------------------------------------------------

    async def set_protection(self, symbol, signal, entry_price, size):

        sl = signal["stop_loss"]
        tp = signal["take_profit"]

        await self.exchange.set_stop_loss(
            symbol,
            signal["side"],
            Decimal(sl),
            Decimal(size)
        )

        await self.exchange.set_take_profit(
            symbol,
            signal["side"],
            Decimal(tp),
            Decimal(size)
        )

        logger.info(f"{symbol} SL set at {sl}")
        logger.info(f"{symbol} TP set at {tp}")

    # --------------------------------------------------
    # CLOSE TRADE
    # --------------------------------------------------

    async def close_trade(self, symbol):

        position = await self.exchange.get_position(symbol)

        if not position:
            return

        result = await self.exchange.close_position(
            symbol,
            position.side,
            position.size
        )

        if not result.success:
            logger.error("Failed closing position")
            return

        db.close_position(position.symbol)

        logger.info(f"Position closed {symbol}")
