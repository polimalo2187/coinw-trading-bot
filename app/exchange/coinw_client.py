import time
import hmac
import hashlib
import logging
from decimal import Decimal
from typing import Optional

import requests

from app.config import config
from app.exchange.base_exchange import (
    BaseExchange,
    Balance,
    Position,
    Kline,
    OrderRequest,
    OrderResult
)

logger = logging.getLogger(__name__)


class CoinWClient(BaseExchange):

    def __init__(self, api_key: str, api_secret: str):

        self.api_key = api_key
        self.api_secret = api_secret

        self.base_url = config.coinw_rest_base_url
        self.timeout = config.coinw_timeout_seconds

    # --------------------------------------------------
    # INTERNAL
    # --------------------------------------------------

    def _timestamp(self) -> int:
        return int(time.time() * 1000)

    def _sign(self, params: dict) -> str:

        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))

        signature = hmac.new(
            self.api_secret.encode(),
            query.encode(),
            hashlib.sha256
        ).hexdigest()

        return signature

    def _request(self, method: str, path: str, params: Optional[dict] = None):

        if params is None:
            params = {}

        params["timestamp"] = self._timestamp()

        params["signature"] = self._sign(params)

        headers = {
            "X-API-KEY": self.api_key
        }

        url = f"{self.base_url}{path}"

        try:

            if method == "GET":

                r = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout
                )

            else:

                r = requests.post(
                    url,
                    json=params,
                    headers=headers,
                    timeout=self.timeout
                )

            r.raise_for_status()

            return r.json()

        except Exception as e:

            logger.error(f"CoinW API error: {e}")

            raise

    # --------------------------------------------------
    # BASIC
    # --------------------------------------------------

    async def ping(self) -> bool:
        try:

            r = requests.get(
                f"{self.base_url}/api/v1/public/ping",
                timeout=self.timeout
            )

            return r.status_code == 200

        except Exception:

            return False

    async def get_price(self, symbol: str) -> Decimal:

        url = f"{self.base_url}/api/v1/public/ticker"

        r = requests.get(
            url,
            params={"symbol": symbol},
            timeout=self.timeout
        )

        data = r.json()

        price = Decimal(str(data["data"]["last"]))

        return price

    async def get_balance(self, asset: str) -> Balance:

        data = self._request(
            "GET",
            "/api/v1/private/account/balance"
        )

        for b in data["data"]:

            if b["asset"] == asset:

                free = Decimal(str(b["free"]))
                locked = Decimal(str(b["locked"]))

                return Balance(
                    asset=asset,
                    free=free,
                    locked=locked,
                    total=free + locked
                )

        return Balance(asset=asset, free=Decimal("0"))

    # --------------------------------------------------
    # MARKET DATA
    # --------------------------------------------------

    async def get_klines(self, symbol: str, interval: str, limit: int = 200):

        url = f"{self.base_url}/api/v1/public/klines"

        r = requests.get(
            url,
            params={
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            },
            timeout=self.timeout
        )

        data = r.json()

        klines = []

        for k in data["data"]:

            klines.append(
                Kline(
                    open_time=k[0],
                    open=Decimal(str(k[1])),
                    high=Decimal(str(k[2])),
                    low=Decimal(str(k[3])),
                    close=Decimal(str(k[4])),
                    volume=Decimal(str(k[5])),
                    close_time=k[6],
                    raw=k
                )
            )

        return klines

    # --------------------------------------------------
    # POSITIONS
    # --------------------------------------------------

    async def get_open_positions(self):

        data = self._request(
            "GET",
            "/api/v1/private/position/list"
        )

        positions = []

        for p in data["data"]:

            size = Decimal(str(p["size"]))

            if size == 0:
                continue

            positions.append(
                Position(
                    symbol=p["symbol"],
                    side=p["side"],
                    size=size,
                    entry_price=Decimal(str(p["entryPrice"])),
                    mark_price=Decimal(str(p["markPrice"])),
                    unrealized_pnl=Decimal(str(p["unrealizedPnl"])),
                    leverage=int(p["leverage"]),
                    raw=p
                )
            )

        return positions

    async def has_open_position(self, symbol: str) -> bool:

        positions = await self.get_open_positions()

        for p in positions:

            if p.symbol == symbol:
                return True

        return False

    async def get_position(self, symbol: str) -> Optional[Position]:

        positions = await self.get_open_positions()

        for p in positions:

            if p.symbol == symbol:
                return p

        return None

    # --------------------------------------------------
    # ORDERS
    # --------------------------------------------------

    async def open_position(self, order: OrderRequest) -> OrderResult:

        params = {
            "symbol": order.symbol,
            "side": order.side,
            "type": order.order_type,
            "quantity": float(order.quantity)
        }

        if order.price:
            params["price"] = float(order.price)

        data = self._request(
            "POST",
            "/api/v1/private/order/create",
            params
        )

        return OrderResult(
            success=True,
            exchange_order_id=str(data["data"]["orderId"]),
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            position_side=order.position_side,
            status="submitted",
            requested_quantity=order.quantity,
            executed_quantity=Decimal("0"),
            raw=data
        )

    async def close_position(self, symbol: str, position_side: str, quantity=None):

        side = "SELL" if position_side == "LONG" else "BUY"

        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET"
        }

        if quantity:
            params["quantity"] = float(quantity)

        data = self._request(
            "POST",
            "/api/v1/private/order/create",
            params
        )

        return OrderResult(
            success=True,
            exchange_order_id=str(data["data"]["orderId"]),
            client_order_id=None,
            symbol=symbol,
            side=side,
            position_side=position_side,
            status="submitted",
            requested_quantity=Decimal(str(quantity)) if quantity else Decimal("0"),
            executed_quantity=Decimal("0"),
            raw=data
        )

    # --------------------------------------------------
    # SL / TP
    # --------------------------------------------------

    async def set_stop_loss(self, symbol, position_side, stop_price, quantity=None):

        params = {
            "symbol": symbol,
            "type": "STOP_MARKET",
            "stopPrice": float(stop_price)
        }

        data = self._request(
            "POST",
            "/api/v1/private/order/create",
            params
        )

        return OrderResult(
            success=True,
            exchange_order_id=str(data["data"]["orderId"]),
            client_order_id=None,
            symbol=symbol,
            side="",
            position_side=position_side,
            status="submitted",
            requested_quantity=Decimal("0"),
            executed_quantity=Decimal("0"),
            raw=data
        )

    async def set_take_profit(self, symbol, position_side, take_profit_price, quantity=None):

        params = {
            "symbol": symbol,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": float(take_profit_price)
        }

        data = self._request(
            "POST",
            "/api/v1/private/order/create",
            params
        )

        return OrderResult(
            success=True,
            exchange_order_id=str(data["data"]["orderId"]),
            client_order_id=None,
            symbol=symbol,
            side="",
            position_side=position_side,
            status="submitted",
            requested_quantity=Decimal("0"),
            executed_quantity=Decimal("0"),
            raw=data
        )

    async def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:

        self._request(
            "POST",
            "/api/v1/private/order/cancel",
            {
                "symbol": symbol,
                "orderId": exchange_order_id
            }
        )

        return True

    async def cancel_all_orders_for_symbol(self, symbol: str) -> bool:

        self._request(
            "POST",
            "/api/v1/private/order/cancelAll",
            {
                "symbol": symbol
            }
        )

        return True

    async def set_leverage(self, symbol: str, leverage: int) -> bool:

        self._request(
            "POST",
            "/api/v1/private/position/setLeverage",
            {
                "symbol": symbol,
                "leverage": leverage
            }
        )

        return True

    async def get_exchange_symbol(self, internal_symbol: str) -> str:
        return internal_symbol
