import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, InvalidOperation

from app.config import config

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PositionSizeResult:
    quantity: Decimal
    risk_amount: Decimal
    stop_distance: Decimal
    notional_value: Decimal
    valid: bool
    reason: str | None = None


class RiskManager:
    """
    Risk manager based on stop-loss distance.

    Core rule:
        risk_amount = balance * risk_per_trade
        qty = risk_amount / abs(entry_price - stop_loss)

    This is the minimum acceptable model for real trading.
    """

    def __init__(self) -> None:
        self.risk_per_trade = Decimal(str(config.risk_per_trade))
        self.max_daily_loss_pct = Decimal(str(config.max_daily_loss_pct))
        self.max_open_positions_per_user = int(config.max_open_positions_per_user)

        # Default precision guard. Later this should come from exchange symbol rules.
        self.default_qty_step = Decimal("0.0001")
        self.min_qty = Decimal("0.0001")
        self.min_notional = Decimal("5")

    # --------------------------------------------------
    # POSITION SIZING
    # --------------------------------------------------

    def calculate_position_size(
        self,
        balance: Decimal,
        entry_price: Decimal,
        stop_loss: Decimal,
        leverage: int | None = None,
        qty_step: Decimal | None = None,
        min_qty: Decimal | None = None,
        min_notional: Decimal | None = None,
    ) -> PositionSizeResult:
        """
        Calculate position size from stop-loss distance.

        Parameters:
            balance: available quote balance
            entry_price: intended entry price
            stop_loss: stop loss price
            leverage: currently unused for risk sizing itself, but kept for future checks
            qty_step: exchange quantity increment
            min_qty: exchange minimum quantity
            min_notional: exchange minimum notional

        Returns:
            PositionSizeResult
        """
        try:
            qty_step = qty_step or self.default_qty_step
            min_qty = min_qty or self.min_qty
            min_notional = min_notional or self.min_notional

            if balance <= 0:
                return self._invalid("Balance must be greater than zero")

            if entry_price <= 0:
                return self._invalid("Entry price must be greater than zero")

            if stop_loss <= 0:
                return self._invalid("Stop loss must be greater than zero")

            stop_distance = abs(entry_price - stop_loss)

            if stop_distance <= 0:
                return self._invalid("Stop distance must be greater than zero")

            risk_amount = balance * self.risk_per_trade

            if risk_amount <= 0:
                return self._invalid("Risk amount must be greater than zero")

            raw_quantity = risk_amount / stop_distance
            quantity = self._round_down(raw_quantity, qty_step)

            if quantity <= 0:
                return self._invalid("Calculated quantity is zero after rounding")

            if quantity < min_qty:
                return self._invalid(
                    f"Calculated quantity below minimum quantity: qty={quantity} min_qty={min_qty}"
                )

            notional_value = quantity * entry_price

            if notional_value < min_notional:
                return self._invalid(
                    f"Calculated notional below minimum notional: notional={notional_value} min_notional={min_notional}"
                )

            logger.info(
                "Risk sizing computed | balance=%s entry=%s stop=%s stop_distance=%s "
                "risk_amount=%s quantity=%s notional=%s leverage=%s",
                balance,
                entry_price,
                stop_loss,
                stop_distance,
                risk_amount,
                quantity,
                notional_value,
                leverage,
            )

            return PositionSizeResult(
                quantity=quantity,
                risk_amount=risk_amount,
                stop_distance=stop_distance,
                notional_value=notional_value,
                valid=True,
                reason=None,
            )

        except (InvalidOperation, ZeroDivisionError) as exc:
            logger.exception("Risk sizing numeric error: %s", exc)
            return self._invalid(f"Numeric sizing error: {exc}")

        except Exception as exc:
            logger.exception("Unexpected risk sizing error: %s", exc)
            return self._invalid(f"Unexpected sizing error: {exc}")

    # --------------------------------------------------
    # VALIDATIONS
    # --------------------------------------------------

    def validate_signal_risk(
        self,
        side: str,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
    ) -> tuple[bool, str | None]:
        """
        Validate directional consistency of the trade setup.
        """

        if entry_price <= 0 or stop_loss <= 0 or take_profit <= 0:
            return False, "Entry, stop loss and take profit must be > 0"

        if side not in {"LONG", "SHORT"}:
            return False, f"Invalid side: {side}"

        if side == "LONG":
            if stop_loss >= entry_price:
                return False, "LONG stop loss must be below entry price"
            if take_profit <= entry_price:
                return False, "LONG take profit must be above entry price"

        if side == "SHORT":
            if stop_loss <= entry_price:
                return False, "SHORT stop loss must be above entry price"
            if take_profit >= entry_price:
                return False, "SHORT take profit must be below entry price"

        return True, None

    def can_open_new_position(
        self,
        current_open_positions: int,
        user_status: str,
    ) -> tuple[bool, str | None]:
        """
        Core pre-trade permission checks.
        """

        if user_status != "active":
            return False, f"User status does not allow trading: {user_status}"

        if current_open_positions >= self.max_open_positions_per_user:
            return (
                False,
                f"Max open positions reached: {current_open_positions}/{self.max_open_positions_per_user}",
            )

        return True, None

    # --------------------------------------------------
    # HELPERS
    # --------------------------------------------------

    def _round_down(self, value: Decimal, step: Decimal) -> Decimal:
        if step <= 0:
            raise ValueError("Step must be greater than zero")

        return (value / step).quantize(Decimal("1"), rounding=ROUND_DOWN) * step

    def _invalid(self, reason: str) -> PositionSizeResult:
        logger.warning("Risk validation failed: %s", reason)
        return PositionSizeResult(
            quantity=Decimal("0"),
            risk_amount=Decimal("0"),
            stop_distance=Decimal("0"),
            notional_value=Decimal("0"),
            valid=False,
            reason=reason,
                                    )
