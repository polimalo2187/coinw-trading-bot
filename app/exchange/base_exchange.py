from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional


@dataclass(slots=True)
class Balance:
    asset: str
    free: Decimal
    locked: Decimal = Decimal("0")
    total: Decimal = Decimal("0")


@dataclass(slots=True)
class Position:
    symbol: str
    side: str  # LONG | SHORT
    size: Decimal
    entry_price: Decimal
    mark_price: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    leverage: int = 1
    raw: Optional[dict[str, Any]] = None


@dataclass(slots=True)
class Kline:
    open_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time: Optional[int] = None
    raw: Optional[dict[str, Any]] = None


@dataclass(slots=True)
class OrderRequest:
    symbol: str
    side: str              # BUY | SELL
    position_side: str     # LONG | SHORT
    quantity: Decimal
    order_type: str = "MARKET"
    price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    reduce_only: bool = False
    client_order_id: Optional[str] = None


@dataclass(slots=True)
class OrderResult:
    success: bool
    exchange_order_id: str
    client_order_id: Optional[str]
    symbol: str
    side: str
    position_side: str
    status: str
    requested_quantity: Decimal
    executed_quantity: Decimal
    price: Optional[Decimal] = None
    average_price: Optional[Decimal] = None
    raw: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None


class BaseExchange(ABC):
    """
    Universal exchange contract used by the trading engine.
    The engine must depend on this interface only.
    """

    @abstractmethod
    async def ping(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def get_balance(self, asset: str) -> Balance:
        raise NotImplementedError

    @abstractmethod
    async def get_price(self, symbol: str) -> Decimal:
        raise NotImplementedError

    @abstractmethod
    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list[Kline]:
        raise NotImplementedError

    @abstractmethod
    async def get_open_positions(self) -> list[Position]:
        raise NotImplementedError

    @abstractmethod
    async def get_position(self, symbol: str) -> Optional[Position]:
        raise NotImplementedError

    @abstractmethod
    async def has_open_position(self, symbol: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def open_position(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    async def close_position(
        self,
        symbol: str,
        position_side: str,
        quantity: Optional[Decimal] = None,
    ) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    async def set_stop_loss(
        self,
        symbol: str,
        position_side: str,
        stop_price: Decimal,
        quantity: Optional[Decimal] = None,
    ) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    async def set_take_profit(
        self,
        symbol: str,
        position_side: str,
        take_profit_price: Decimal,
        quantity: Optional[Decimal] = None,
    ) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    async def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def cancel_all_orders_for_symbol(self, symbol: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def get_exchange_symbol(self, internal_symbol: str) -> str:
        """
        Maps internal symbol name to exchange-specific symbol if needed.
        Example: BTCUSDT -> BTC_USDT or BTC-USDT depending on exchange.
        """
        raise NotImplementedError
