import logging
from decimal import Decimal
from typing import Any, Optional

from app.config import config
from app.database import db
from app.exchange.base_exchange import OrderRequest
from app.exchange.coinw_client import CoinWClient
from app.risk import RiskManager
from app.strategy import Strategy

logger = logging.getLogger(__name__)


class TradingEngine:
    """
    Trading engine responsible for:
    - receiving market data
    - generating signals
    - validating risk
    - executing orders
    - placing protection
    - recording positions in database
    """

    def __init__(self, user: dict[str, Any]) -> None:
        self.user = user

        self.exchange = CoinWClient(
            api_key=user["api_key"],
            api_secret=user["api_secret"],
        )

        self.strategy = Strategy()
        self.risk = RiskManager()

    # --------------------------------------------------
    # PUBLIC
    # --------------------------------------------------

    async def process_symbol(self, symbol: str, klines: list[Any]) -> None:
        """
        Main per-symbol processing entrypoint.
        """
        try:
            if not klines:
                logger.debug("No klines received for %s", symbol)
                return

            user_status = self.user.get("status", "unknown")

            current_open_positions = len(
                db.get_open_positions(self.user["_id"])
            )

            can_open, reason = self.risk.can_open_new_position(
                current_open_positions=current_open_positions,
                user_status=user_status,
            )

            if not can_open:
                logger.info(
                    "User %s cannot open new position on %s: %s",
                    self.user.get("telegram_id"),
                    symbol,
                    reason,
                )
                return

            signal = self.strategy.generate_signal(symbol, klines)

            if not signal:
                return

            has_position = await self.exchange.has_open_position(symbol)
            if has_position:
                logger.info(
                    "Skipping %s for user %s because position is already open",
                    symbol,
                    self.user.get("telegram_id"),
                )
                return

            await self.open_trade(symbol=symbol, signal=signal)

        except Exception:
            logger.exception(
                "TradingEngine.process_symbol failed for user=%s symbol=%s",
                self.user.get("telegram_id"),
                symbol,
            )

    # --------------------------------------------------
    # OPEN TRADE
    # --------------------------------------------------

    async def open_trade(self, symbol: str, signal: dict[str, Any]) -> None:
        try:
            logger.info(
                "Preparing trade | user=%s symbol=%s side=%s",
                self.user.get("telegram_id"),
                symbol,
                signal["side"],
            )

            price = await self.exchange.get_price(symbol)
            balance = await self.exchange.get_balance(config.default_quote_asset)

            entry_price = Decimal(str(price))
            stop_loss = Decimal(str(signal["stop_loss"]))
            take_profit = Decimal(str(signal["take_profit"]))

            is_valid, risk_reason = self.risk.validate_signal_risk(
                side=signal["side"],
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

            if not is_valid:
                logger.warning(
                    "Rejected signal by risk validation | user=%s symbol=%s reason=%s",
                    self.user.get("telegram_id"),
                    symbol,
                    risk_reason,
                )
                return

            sizing = self.risk.calculate_position_size(
                balance=balance.free,
                entry_price=entry_price,
                stop_loss=stop_loss,
                leverage=config.default_leverage,
            )

            if not sizing.valid:
                logger.warning(
                    "Rejected trade by position sizing | user=%s symbol=%s reason=%s",
                    self.user.get("telegram_id"),
                    symbol,
                    sizing.reason,
                )
                return

            quantity = sizing.quantity
            if quantity <= 0:
                logger.warning(
                    "Rejected trade because quantity <= 0 | user=%s symbol=%s",
                    self.user.get("telegram_id"),
                    symbol,
                )
                return

            side = "BUY" if signal["side"] == "LONG" else "SELL"

            leverage_ok = await self.exchange.set_leverage(
                symbol=symbol,
                leverage=config.default_leverage,
            )

            if not leverage_ok:
                logger.warning(
                    "Could not set leverage | user=%s symbol=%s leverage=%s",
                    self.user.get("telegram_id"),
                    symbol,
                    config.default_leverage,
                )
                return

            order = OrderRequest(
                symbol=symbol,
                side=side,
                position_side=signal["side"],
                quantity=quantity,
                order_type="MARKET",
            )

            result = await self.exchange.open_position(order)

            if not result.success:
                logger.error(
                    "Open position failed | user=%s symbol=%s error=%s",
                    self.user.get("telegram_id"),
                    symbol,
                    result.error_message,
                )
                return

            logger.info(
                "Order submitted | user=%s symbol=%s exchange_order_id=%s qty=%s",
                self.user.get("telegram_id"),
                symbol,
                result.exchange_order_id,
                result.requested_quantity,
            )

            confirmed_position = None
            if config.confirm_position_after_order:
                confirmed_position = await self.exchange.get_position(symbol)

                if not confirmed_position:
                    logger.warning(
                        "Order submitted but no confirmed position found yet | user=%s symbol=%s",
                        self.user.get("telegram_id"),
                        symbol,
                    )

            protection_ok = await self.set_protection(
                symbol=symbol,
                signal=signal,
                quantity=quantity,
            )

            if not protection_ok:
                logger.warning(
                    "Protection setup incomplete | user=%s symbol=%s",
                    self.user.get("telegram_id"),
                    symbol,
                )

            db.create_position(
                {
                    "user_id": self.user["_id"],
                    "telegram_id": self.user.get("telegram_id"),
                    "symbol": symbol,
                    "side": signal["side"],
                    "size": float(quantity),
                    "entry_price": float(entry_price),
                    "stop_loss": float(stop_loss),
                    "take_profit": float(take_profit),
                    "risk_amount": float(sizing.risk_amount),
                    "stop_distance": float(sizing.stop_distance),
                    "notional_value": float(sizing.notional_value),
                    "exchange_order_id": result.exchange_order_id,
                    "exchange_status": result.status,
                    "status": "open",
                    "opened_at": db._now(),
                    "raw_order": result.raw,
                    "raw_position": confirmed_position.raw if confirmed_position else None,
                }
            )

            logger.info(
                "Position recorded in DB | user=%s symbol=%s side=%s qty=%s",
                self.user.get("telegram_id"),
                symbol,
                signal["side"],
                quantity,
            )

        except Exception:
            logger.exception(
                "TradingEngine.open_trade failed | user=%s symbol=%s",
                self.user.get("telegram_id"),
                symbol,
            )

    # --------------------------------------------------
    # PROTECTION
    # --------------------------------------------------

    async def set_protection(
        self,
        symbol: str,
        signal: dict[str, Any],
        quantity: Decimal,
    ) -> bool:
        """
        Set stop loss and take profit.
        Returns True only if both operations succeeded.
        """
        try:
            stop_loss = Decimal(str(signal["stop_loss"]))
            take_profit = Decimal(str(signal["take_profit"]))
            position_side = signal["side"]

            sl_result = await self.exchange.set_stop_loss(
                symbol=symbol,
                position_side=position_side,
                stop_price=stop_loss,
                quantity=quantity,
            )

            tp_result = await self.exchange.set_take_profit(
                symbol=symbol,
                position_side=position_side,
                take_profit_price=take_profit,
                quantity=quantity,
            )

            sl_ok = sl_result.success
            tp_ok = tp_result.success

            if sl_ok:
                logger.info(
                    "Stop loss set | user=%s symbol=%s stop=%s",
                    self.user.get("telegram_id"),
                    symbol,
                    stop_loss,
                )
            else:
                logger.error(
                    "Failed setting stop loss | user=%s symbol=%s error=%s",
                    self.user.get("telegram_id"),
                    symbol,
                    sl_result.error_message,
                )

            if tp_ok:
                logger.info(
                    "Take profit set | user=%s symbol=%s tp=%s",
                    self.user.get("telegram_id"),
                    symbol,
                    take_profit,
                )
            else:
                logger.error(
                    "Failed setting take profit | user=%s symbol=%s error=%s",
                    self.user.get("telegram_id"),
                    symbol,
                    tp_result.error_message,
                )

            return sl_ok and tp_ok

        except Exception:
            logger.exception(
                "TradingEngine.set_protection failed | user=%s symbol=%s",
                self.user.get("telegram_id"),
                symbol,
            )
            return False

    # --------------------------------------------------
    # CLOSE TRADE
    # --------------------------------------------------

    async def close_trade(self, symbol: str) -> None:
        try:
            position = await self.exchange.get_position(symbol)

            if not position:
                logger.info(
                    "No exchange position found to close | user=%s symbol=%s",
                    self.user.get("telegram_id"),
                    symbol,
                )
                return

            result = await self.exchange.close_position(
                symbol=symbol,
                position_side=position.side,
                quantity=position.size,
            )

            if not result.success:
                logger.error(
                    "Close position failed | user=%s symbol=%s error=%s",
                    self.user.get("telegram_id"),
                    symbol,
                    result.error_message,
                )
                return

            db_position = self._get_latest_open_db_position(symbol)
            if db_position:
                db.close_position(db_position["_id"])

            logger.info(
                "Position closed | user=%s symbol=%s exchange_order_id=%s",
                self.user.get("telegram_id"),
                symbol,
                result.exchange_order_id,
            )

        except Exception:
            logger.exception(
                "TradingEngine.close_trade failed | user=%s symbol=%s",
                self.user.get("telegram_id"),
                symbol,
            )

    # --------------------------------------------------
    # HELPERS
    # --------------------------------------------------

    def _get_latest_open_db_position(self, symbol: str) -> Optional[dict[str, Any]]:
        open_positions = db.get_open_positions(self.user["_id"])

        matching = [
            p for p in open_positions
            if p.get("symbol") == symbol and p.get("status") == "open"
        ]

        if not matching:
            return None

        matching.sort(key=lambda x: x.get("opened_at", db._now()), reverse=True)
        return matching[0]
