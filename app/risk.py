import logging
from decimal import Decimal

from app.config import config

logger = logging.getLogger(__name__)


class RiskManager:

    def __init__(self):

        self.risk_per_trade = Decimal(str(config.risk_per_trade))

    # --------------------------------------------------
    # POSITION SIZE
    # --------------------------------------------------

    def calculate_position_size(
        self,
        balance: Decimal,
        price: Decimal
    ) -> Decimal:

        try:

            if balance <= 0:
                logger.warning("Balance is zero")
                return Decimal("0")

            risk_amount = balance * self.risk_per_trade

            size = risk_amount / price

            size = size.quantize(Decimal("0.0001"))

            logger.info(
                f"Position size calculated balance={balance} price={price} size={size}"
            )

            return size

        except Exception as e:

            logger.exception(f"Position sizing error: {e}")

            return Decimal("0")
