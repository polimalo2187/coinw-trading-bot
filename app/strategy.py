import logging
from dataclasses import dataclass
from decimal import Decimal
from statistics import mean
from typing import Optional, List

from app.exchange.base_exchange import Kline

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Signal:
    symbol: str
    side: str  # LONG | SHORT
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    reason: str
    confidence: float


class Strategy:
    """
    Baseline strategy module compatible with the current trading engine.

    This is intentionally clean and deterministic:
    - Trend filter with EMA
    - Momentum filter with RSI
    - Volatility-aware SL/TP using ATR
    - Rejects weak / noisy setups

    IMPORTANT:
    This is a safe base strategy module, not yet the exact migrated logic
    from the user's current production bot.
    """

    def __init__(self) -> None:
        self.fast_ema_period = 20
        self.slow_ema_period = 50
        self.rsi_period = 14
        self.atr_period = 14

        self.min_candles = 80

        self.long_rsi_min = 55.0
        self.short_rsi_max = 45.0

        self.stop_atr_multiplier = Decimal("1.5")
        self.tp_atr_multiplier = Decimal("2.5")

        self.min_atr_pct = Decimal("0.0015")   # 0.15%
        self.max_atr_pct = Decimal("0.08")     # 8%

    # --------------------------------------------------
    # PUBLIC
    # --------------------------------------------------

    def generate_signal(self, symbol: str, klines: List[Kline]) -> Optional[dict]:
        try:
            if not klines or len(klines) < self.min_candles:
                logger.debug(
                    "Not enough candles for %s. Required=%s got=%s",
                    symbol,
                    self.min_candles,
                    len(klines) if klines else 0,
                )
                return None

            closes = [self._to_float(k.close) for k in klines]
            highs = [self._to_float(k.high) for k in klines]
            lows = [self._to_float(k.low) for k in klines]

            current_close = Decimal(str(closes[-1]))

            fast_ema = self._ema(closes, self.fast_ema_period)
            slow_ema = self._ema(closes, self.slow_ema_period)
            rsi = self._rsi(closes, self.rsi_period)
            atr = Decimal(str(self._atr(highs, lows, closes, self.atr_period)))

            if fast_ema is None or slow_ema is None or rsi is None or atr <= 0:
                return None

            fast_ema_d = Decimal(str(fast_ema))
            slow_ema_d = Decimal(str(slow_ema))
            rsi_d = Decimal(str(rsi))

            atr_pct = atr / current_close if current_close > 0 else Decimal("0")

            # Reject dead/noisy markets
            if atr_pct < self.min_atr_pct:
                logger.debug("%s rejected: ATR too low (%s)", symbol, atr_pct)
                return None

            if atr_pct > self.max_atr_pct:
                logger.debug("%s rejected: ATR too high (%s)", symbol, atr_pct)
                return None

            # LONG setup
            if fast_ema_d > slow_ema_d and rsi >= self.long_rsi_min:
                stop_loss = current_close - (atr * self.stop_atr_multiplier)
                take_profit = current_close + (atr * self.tp_atr_multiplier)

                signal = Signal(
                    symbol=symbol,
                    side="LONG",
                    entry_price=current_close,
                    stop_loss=self._quantize_price(stop_loss),
                    take_profit=self._quantize_price(take_profit),
                    reason=(
                        f"bullish_trend fast_ema={fast_ema_d} slow_ema={slow_ema_d} "
                        f"rsi={rsi_d} atr={atr}"
                    ),
                    confidence=self._confidence_long(fast_ema_d, slow_ema_d, rsi_d, atr_pct),
                )

                logger.info(
                    "LONG signal %s | entry=%s sl=%s tp=%s conf=%.2f",
                    symbol,
                    signal.entry_price,
                    signal.stop_loss,
                    signal.take_profit,
                    signal.confidence,
                )

                return self._signal_to_dict(signal)

            # SHORT setup
            if fast_ema_d < slow_ema_d and rsi <= self.short_rsi_max:
                stop_loss = current_close + (atr * self.stop_atr_multiplier)
                take_profit = current_close - (atr * self.tp_atr_multiplier)

                signal = Signal(
                    symbol=symbol,
                    side="SHORT",
                    entry_price=current_close,
                    stop_loss=self._quantize_price(stop_loss),
                    take_profit=self._quantize_price(take_profit),
                    reason=(
                        f"bearish_trend fast_ema={fast_ema_d} slow_ema={slow_ema_d} "
                        f"rsi={rsi_d} atr={atr}"
                    ),
                    confidence=self._confidence_short(fast_ema_d, slow_ema_d, rsi_d, atr_pct),
                )

                logger.info(
                    "SHORT signal %s | entry=%s sl=%s tp=%s conf=%.2f",
                    symbol,
                    signal.entry_price,
                    signal.stop_loss,
                    signal.take_profit,
                    signal.confidence,
                )

                return self._signal_to_dict(signal)

            return None

        except Exception:
            logger.exception("Strategy failed for symbol=%s", symbol)
            return None

    # --------------------------------------------------
    # CONFIDENCE
    # --------------------------------------------------

    def _confidence_long(
        self,
        fast_ema: Decimal,
        slow_ema: Decimal,
        rsi: Decimal,
        atr_pct: Decimal,
    ) -> float:
        trend_strength = float((fast_ema - slow_ema) / slow_ema) if slow_ema > 0 else 0.0
        rsi_strength = min(max((float(rsi) - 50.0) / 20.0, 0.0), 1.0)
        vol_score = 1.0 - min(max(float(atr_pct) / 0.05, 0.0), 1.0) * 0.35

        score = (trend_strength * 6.0) + (rsi_strength * 0.7) + (vol_score * 0.3)
        return round(max(min(score, 0.99), 0.05), 4)

    def _confidence_short(
        self,
        fast_ema: Decimal,
        slow_ema: Decimal,
        rsi: Decimal,
        atr_pct: Decimal,
    ) -> float:
        trend_strength = float((slow_ema - fast_ema) / slow_ema) if slow_ema > 0 else 0.0
        rsi_strength = min(max((50.0 - float(rsi)) / 20.0, 0.0), 1.0)
        vol_score = 1.0 - min(max(float(atr_pct) / 0.05, 0.0), 1.0) * 0.35

        score = (trend_strength * 6.0) + (rsi_strength * 0.7) + (vol_score * 0.3)
        return round(max(min(score, 0.99), 0.05), 4)

    # --------------------------------------------------
    # INDICATORS
    # --------------------------------------------------

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None

        multiplier = 2 / (period + 1)
        ema = mean(values[:period])

        for value in values[period:]:
            ema = ((value - ema) * multiplier) + ema

        return ema

    def _rsi(self, values: List[float], period: int) -> Optional[float]:
        if len(values) <= period:
            return None

        gains = []
        losses = []

        for i in range(1, period + 1):
            delta = values[i] - values[i - 1]
            gains.append(max(delta, 0.0))
            losses.append(abs(min(delta, 0.0)))

        avg_gain = mean(gains)
        avg_loss = mean(losses)

        for i in range(period + 1, len(values)):
            delta = values[i] - values[i - 1]
            gain = max(delta, 0.0)
            loss = abs(min(delta, 0.0))

            avg_gain = ((avg_gain * (period - 1)) + gain) / period
            avg_loss = ((avg_loss * (period - 1)) + loss) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _atr(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int,
    ) -> Optional[float]:
        if len(highs) <= period or len(lows) <= period or len(closes) <= period:
            return None

        true_ranges = []

        for i in range(1, len(closes)):
            high = highs[i]
            low = lows[i]
            prev_close = closes[i - 1]

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            true_ranges.append(tr)

        if len(true_ranges) < period:
            return None

        atr = mean(true_ranges[:period])

        for tr in true_ranges[period:]:
            atr = ((atr * (period - 1)) + tr) / period

        return atr

    # --------------------------------------------------
    # HELPERS
    # --------------------------------------------------

    def _signal_to_dict(self, signal: Signal) -> dict:
        return {
            "symbol": signal.symbol,
            "side": signal.side,
            "entry_price": signal.entry_price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "reason": signal.reason,
            "confidence": signal.confidence,
        }

    def _quantize_price(self, value: Decimal) -> Decimal:
        if value <= 0:
            return Decimal("0")
        return value.quantize(Decimal("0.0001"))

    def _to_float(self, value: Decimal) -> float:
        return float(value)
