import base64
import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import requests

from app.config import config
from app.exchange.base_exchange import (
    Balance,
    BaseExchange,
    Kline,
    OrderRequest,
    OrderResult,
    Position,
)

logger = logging.getLogger(__name__)


class CoinWClient(BaseExchange):
    """
    CoinW Futures client.

    Important implementation notes:
    - Private REST auth follows CoinW's documented format:
      sign = Base64(HMAC_SHA256(timestamp + METHOD + api_path + query_or_json_body))
    - Private headers:
      sign, api_key, timestamp
    - Base URL: https://api.coinw.com
    """

    _GRANULARITY_MAP = {
        "1m": "0",
        "5m": "1",
        "15m": "2",
        "1h": "3",
        "4h": "4",
        "1d": "5",
        "1w": "6",
        "3m": "7",
        "30m": "8",
        "1M": "9",
    }

    def __init__(self, api_key: str, api_secret: str) -> None:
        self.api_key = (api_key or "").strip()
        self.api_secret = (api_secret or "").strip()

        self.base_url = config.coinw_rest_base_url.rstrip("/")
        self.timeout = int(config.coinw_timeout_seconds)

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": f"{config.app_name}/1.0",
                "Accept": "application/json",
            }
        )

        # CoinW effectively wants leverage supplied in the order placement payload.
        # We store preferred leverage per symbol here and inject it into open_position().
        self._symbol_leverage: dict[str, int] = {}

    # --------------------------------------------------
    # PUBLIC
    # --------------------------------------------------

    async def ping(self) -> bool:
        try:
            # Use a public endpoint that is documented and stable.
            response = self.session.get(
                f"{self.base_url}/v1/perpumPublic/tickers",
                timeout=self.timeout,
            )
            return response.status_code == 200
        except Exception:
            logger.exception("CoinW ping failed")
            return False

    async def get_balance(self, asset: str) -> Balance:
        """
        Reads futures account assets.

        CoinW docs expose a futures assets endpoint in the official code snippets.
        The response structure may vary a bit across accounts, so parsing here is defensive.
        """
        data = self._private_request("GET", "/v1/perpum/account/getUserAssets", params={})

        assets = self._extract_data_list(data)
        asset_upper = asset.upper()

        for item in assets:
            coin = str(
                item.get("coin")
                or item.get("asset")
                or item.get("currency")
                or item.get("unit")
                or ""
            ).upper()

            if coin != asset_upper:
                continue

            free = self._safe_decimal(
                item.get("available")
                or item.get("canUseAmount")
                or item.get("balance")
                or item.get("free")
                or item.get("usable")
                or "0"
            )
            locked = self._safe_decimal(
                item.get("freeze")
                or item.get("frozen")
                or item.get("lock")
                or item.get("locked")
                or "0"
            )
            total = self._safe_decimal(
                item.get("balance")
                or item.get("total")
                or (free + locked)
            )

            return Balance(
                asset=asset_upper,
                free=free,
                locked=locked,
                total=total if total > 0 else (free + locked),
            )

        logger.warning("Asset %s not found in CoinW futures assets response", asset_upper)
        return Balance(asset=asset_upper, free=Decimal("0"), locked=Decimal("0"), total=Decimal("0"))

    async def get_price(self, symbol: str) -> Decimal:
        """
        Returns last traded price from the public futures tickers endpoint.
        """
        response = self._public_request("GET", "/v1/perpumPublic/tickers", params={})
        rows = self._extract_data_list(response)

        wanted = symbol.upper()
        for row in rows:
            name = str(row.get("name") or "").upper()
            if name == wanted:
                last_price = self._safe_decimal(row.get("last_price") or row.get("lastPrice") or "0")
                if last_price > 0:
                    return last_price

        raise RuntimeError(f"CoinW ticker not found for symbol={symbol}")

    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list[Kline]:
        instrument = self._symbol_to_instrument(symbol)
        granularity = self._map_interval(interval)

        params = {
            "currencyCode": instrument,
            "granuality": granularity,
            "limit": max(1, min(int(limit), 1500)),
            "klineType": "0",  # UTC
        }

        response = self._public_request("GET", "/v1/perpumPublic/klines", params=params)
        raw_rows = response.get("data", response)

        klines: list[Kline] = []

        if not isinstance(raw_rows, list):
            logger.warning("Unexpected CoinW kline payload for %s: %s", symbol, response)
            return klines

        for row in raw_rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue

            # CoinW docs list:
            # timestamp, high, open, low, close, volume
            # We normalize to our Kline dataclass.
            open_time = int(row[0])
            high = self._safe_decimal(row[1])
            open_price = self._safe_decimal(row[2])
            low = self._safe_decimal(row[3])
            close = self._safe_decimal(row[4])
            volume = self._safe_decimal(row[5])

            klines.append(
                Kline(
                    open_time=open_time,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    close_time=None,
                    raw={"row": row},
                )
            )

        return klines

    async def get_open_positions(self) -> list[Position]:
        response = self._private_request("GET", "/v1/perpum/positions/all", params={})
        rows = self._extract_data_list(response)

        positions: list[Position] = []

        for row in rows:
            status = str(row.get("status") or "").lower()
            if status and status != "open":
                continue

            size = self._parse_position_size(row)
            if size <= 0:
                continue

            instrument = str(row.get("instrument") or "").upper()
            side = self._normalize_position_side(row.get("direction"))

            position = Position(
                symbol=self._instrument_to_symbol(instrument),
                side=side,
                size=size,
                entry_price=self._safe_decimal(row.get("openPrice") or row.get("orderPrice") or "0"),
                mark_price=self._safe_decimal(row.get("indexPrice") or row.get("fairPrice") or "0"),
                unrealized_pnl=self._safe_decimal(row.get("profitUnreal") or "0"),
                leverage=int(self._safe_decimal(row.get("leverage") or "1")),
                raw=row,
            )
            positions.append(position)

        return positions

    async def get_position(self, symbol: str) -> Optional[Position]:
        wanted = symbol.upper()
        positions = await self.get_open_positions()

        for pos in positions:
            if pos.symbol.upper() == wanted:
                return pos

        return None

    async def has_open_position(self, symbol: str) -> bool:
        pos = await self.get_position(symbol)
        return pos is not None

    async def open_position(self, order: OrderRequest) -> OrderResult:
        instrument = self._symbol_to_instrument(order.symbol)
        direction = self._position_side_to_direction(order.position_side)
        leverage = self._symbol_leverage.get(order.symbol.upper(), int(config.default_leverage))

        params: dict[str, Any] = {
            "instrument": instrument,
            "direction": direction,
            "leverage": leverage,
            "quantityUnit": 2,  # base currency units; our risk engine sizes in base asset
            "quantity": self._decimal_to_str(order.quantity),
            "positionModel": 0,  # isolated
            "positionType": "execute" if order.order_type.upper() == "MARKET" else "plan",
        }

        if order.client_order_id:
            params["thirdOrderId"] = order.client_order_id

        if order.order_type.upper() != "MARKET":
            if order.price is None:
                return self._order_error(
                    order=order,
                    message="Limit/plan order requires price",
                )
            params["openPrice"] = self._decimal_to_str(order.price)

        response = self._private_request("POST", "/v1/perpum/order", params=params)

        code = response.get("code")
        data = response.get("data", {})
        order_id = self._extract_order_id(data)

        # CoinW explicitly says code=0/orderId means accepted, not filled.
        success = code == 0 and bool(order_id)

        return OrderResult(
            success=success,
            exchange_order_id=order_id,
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            position_side=order.position_side,
            status="submitted" if success else "rejected",
            requested_quantity=order.quantity,
            executed_quantity=Decimal("0"),
            price=order.price,
            average_price=None,
            raw=response,
            error_message=None if success else self._extract_error_message(response),
        )

    async def close_position(
        self,
        symbol: str,
        position_side: str,
        quantity: Optional[Decimal] = None,
    ) -> OrderResult:
        """
        CoinW has a dedicated market-close-by-instrument endpoint.

        Warning:
        This closes all positions for the instrument, not a partial quantity.
        That matches the current bot architecture better than faking partial closes.
        """
        instrument = self._symbol_to_instrument(symbol)

        response = self._private_request(
            "DELETE",
            "/v1/perpum/allpositions",
            params={"instrument": instrument},
        )

        success = response.get("code") == 0

        # We keep the response compatible with OrderResult even though this is a close action.
        return OrderResult(
            success=success,
            exchange_order_id="CLOSE_ALL_MARKET",
            client_order_id=None,
            symbol=symbol,
            side="SELL" if position_side == "LONG" else "BUY",
            position_side=position_side,
            status="submitted" if success else "rejected",
            requested_quantity=quantity or Decimal("0"),
            executed_quantity=Decimal("0"),
            price=None,
            average_price=None,
            raw=response,
            error_message=None if success else self._extract_error_message(response),
        )

    async def set_stop_loss(
        self,
        symbol: str,
        position_side: str,
        stop_price: Decimal,
        quantity: Optional[Decimal] = None,
    ) -> OrderResult:
        position = await self._resolve_current_position(symbol=symbol, position_side=position_side)
        if not position:
            return OrderResult(
                success=False,
                exchange_order_id="",
                client_order_id=None,
                symbol=symbol,
                side="",
                position_side=position_side,
                status="rejected",
                requested_quantity=quantity or Decimal("0"),
                executed_quantity=Decimal("0"),
                raw=None,
                error_message="Could not resolve current CoinW position ID for stop loss",
            )

        position_id = str(position["id"])
        instrument = self._symbol_to_instrument(symbol)

        response = self._private_request(
            "POST",
            "/v1/perpum/TPSL",
            params={
                "id": position_id,
                "instrument": instrument,
                "stopLossPrice": self._decimal_to_str(stop_price),
            },
        )

        success = response.get("code") == 0
        return OrderResult(
            success=success,
            exchange_order_id=position_id,
            client_order_id=None,
            symbol=symbol,
            side="",
            position_side=position_side,
            status="submitted" if success else "rejected",
            requested_quantity=quantity or Decimal("0"),
            executed_quantity=Decimal("0"),
            raw=response,
            error_message=None if success else self._extract_error_message(response),
        )

    async def set_take_profit(
        self,
        symbol: str,
        position_side: str,
        take_profit_price: Decimal,
        quantity: Optional[Decimal] = None,
    ) -> OrderResult:
        position = await self._resolve_current_position(symbol=symbol, position_side=position_side)
        if not position:
            return OrderResult(
                success=False,
                exchange_order_id="",
                client_order_id=None,
                symbol=symbol,
                side="",
                position_side=position_side,
                status="rejected",
                requested_quantity=quantity or Decimal("0"),
                executed_quantity=Decimal("0"),
                raw=None,
                error_message="Could not resolve current CoinW position ID for take profit",
            )

        position_id = str(position["id"])
        instrument = self._symbol_to_instrument(symbol)

        response = self._private_request(
            "POST",
            "/v1/perpum/TPSL",
            params={
                "id": position_id,
                "instrument": instrument,
                "stopProfitPrice": self._decimal_to_str(take_profit_price),
            },
        )

        success = response.get("code") == 0
        return OrderResult(
            success=success,
            exchange_order_id=position_id,
            client_order_id=None,
            symbol=symbol,
            side="",
            position_side=position_side,
            status="submitted" if success else "rejected",
            requested_quantity=quantity or Decimal("0"),
            executed_quantity=Decimal("0"),
            raw=response,
            error_message=None if success else self._extract_error_message(response),
        )

    async def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        response = self._private_request(
            "DELETE",
            "/v1/perpum/order",
            params={"id": exchange_order_id},
        )
        return response.get("code") == 0

    async def cancel_all_orders_for_symbol(self, symbol: str) -> bool:
        """
        I am intentionally not faking this.
        CoinW has batch cancel APIs, but this bot does not yet have a stable
        open-order enumeration + symbol-scoped cancel flow wired in.
        """
        logger.warning("cancel_all_orders_for_symbol is not implemented safely for CoinW yet")
        return False

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        CoinW order placement requires leverage in the order payload.
        We persist desired leverage per symbol here and use it in open_position().
        """
        if leverage <= 0:
            logger.warning("Invalid leverage=%s for symbol=%s", leverage, symbol)
            return False

        self._symbol_leverage[symbol.upper()] = int(leverage)
        return True

    async def get_exchange_symbol(self, internal_symbol: str) -> str:
        return internal_symbol.upper()

    # --------------------------------------------------
    # HTTP LAYER
    # --------------------------------------------------

    def _public_request(self, method: str, api_path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        params = params or {}
        url = f"{self.base_url}{api_path}"

        try:
            response = self.session.request(
                method=method.upper(),
                url=url,
                params=params if method.upper() == "GET" else None,
                json=params if method.upper() != "GET" else None,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as exc:
            body = self._safe_response_text(exc.response)
            logger.error("CoinW public HTTP error | method=%s path=%s body=%s", method, api_path, body)
            raise RuntimeError(f"CoinW public HTTP error: {body}") from exc
        except Exception as exc:
            logger.exception("CoinW public request failed | method=%s path=%s", method, api_path)
            raise RuntimeError(f"CoinW public request failed: {exc}") from exc

    def _private_request(self, method: str, api_path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("CoinW API credentials are missing")

        method_upper = method.upper()
        params = {k: v for k, v in (params or {}).items() if v is not None}
        timestamp = str(int(time.time() * 1000))

        encoded = self._build_sign_payload(
            timestamp=timestamp,
            method=method_upper,
            api_path=api_path,
            params=params,
        )
        signature = self._sign(encoded)

        headers = {
            "sign": signature,
            "api_key": self.api_key,
            "timestamp": timestamp,
        }
        if method_upper in {"POST", "PUT", "DELETE"}:
            headers["Content-type"] = "application/json"

        url = f"{self.base_url}{api_path}"

        try:
            response = self.session.request(
                method=method_upper,
                url=url,
                params=params if method_upper == "GET" else None,
                data=json.dumps(params, separators=(",", ":"), ensure_ascii=False) if method_upper in {"POST", "PUT", "DELETE"} else None,
                headers=headers,
                timeout=self.timeout,
            )

            response.raise_for_status()
            payload = response.json()

            # CoinW business-level errors come back in JSON with code != 0.
            if isinstance(payload, dict) and payload.get("code") not in (0, "0", None):
                logger.warning(
                    "CoinW business error | method=%s path=%s code=%s msg=%s",
                    method_upper,
                    api_path,
                    payload.get("code"),
                    payload.get("msg"),
                )

            return payload

        except requests.HTTPError as exc:
            body = self._safe_response_text(exc.response)
            logger.error(
                "CoinW private HTTP error | method=%s path=%s body=%s",
                method_upper,
                api_path,
                body,
            )
            raise RuntimeError(f"CoinW private HTTP error: {body}") from exc

        except Exception as exc:
            logger.exception(
                "CoinW private request failed | method=%s path=%s",
                method_upper,
                api_path,
            )
            raise RuntimeError(f"CoinW private request failed: {exc}") from exc

    def _build_sign_payload(
        self,
        timestamp: str,
        method: str,
        api_path: str,
        params: dict[str, Any],
    ) -> str:
        if method == "GET":
            query = "&".join(
                f"{key}={value}"
                for key, value in params.items()
                if value is not None
            )
            return f"{timestamp}{method}{api_path}?{query}" if query else f"{timestamp}{method}{api_path}"

        body = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
        return f"{timestamp}{method}{api_path}{body}"

    def _sign(self, payload: str) -> str:
        digest = hmac.new(
            self.api_secret.encode("utf-8"),
            msg=payload.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("ascii")

    # --------------------------------------------------
    # HELPERS
    # --------------------------------------------------

    async def _resolve_current_position(self, symbol: str, position_side: str) -> Optional[dict[str, Any]]:
        response = self._private_request("GET", "/v1/perpum/positions/all", params={})
        rows = self._extract_data_list(response)

        wanted_instrument = self._symbol_to_instrument(symbol)
        wanted_side = self._position_side_to_direction(position_side)

        matches: list[dict[str, Any]] = []
        for row in rows:
            instrument = str(row.get("instrument") or "").upper()
            direction = str(row.get("direction") or "").lower()
            status = str(row.get("status") or "").lower()

            if instrument != wanted_instrument:
                continue
            if direction != wanted_side:
                continue
            if status and status != "open":
                continue

            size = self._parse_position_size(row)
            if size <= 0:
                continue

            matches.append(row)

        if not matches:
            return None

        matches.sort(key=lambda x: int(x.get("updatedDate") or x.get("createdDate") or 0), reverse=True)
        return matches[0]

    def _extract_data_list(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data", [])
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # Some CoinW endpoints may wrap list-like info in sub-keys.
            for key in ("list", "rows", "items", "records", "value"):
                candidate = data.get(key)
                if isinstance(candidate, list):
                    return candidate
            return [data]
        return []

    def _extract_order_id(self, data: Any) -> str:
        if isinstance(data, dict):
            value = data.get("value") or data.get("id") or data.get("orderId")
            return "" if value is None else str(value)
        if data is None:
            return ""
        return str(data)

    def _extract_error_message(self, payload: Any) -> str:
        if isinstance(payload, dict):
            return str(payload.get("msg") or payload.get("message") or "Unknown CoinW error")
        return "Unknown CoinW error"

    def _safe_decimal(self, value: Any) -> Decimal:
        try:
            if value is None or value == "":
                return Decimal("0")
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return Decimal("0")

    def _decimal_to_str(self, value: Decimal) -> str:
        normalized = value.normalize()
        return format(normalized, "f")

    def _safe_response_text(self, response: Optional[requests.Response]) -> str:
        if response is None:
            return ""
        try:
            return response.text[:1000]
        except Exception:
            return ""

    def _map_interval(self, interval: str) -> str:
        mapped = self._GRANULARITY_MAP.get(interval)
        if mapped is None:
            raise RuntimeError(f"Unsupported CoinW interval: {interval}")
        return mapped

    def _symbol_to_instrument(self, symbol: str) -> str:
        upper = symbol.upper()
        if upper.endswith("USDT"):
            return upper[:-4]
        raise RuntimeError(f"Unsupported internal symbol format for CoinW futures: {symbol}")

    def _instrument_to_symbol(self, instrument: str) -> str:
        inst = instrument.upper()
        if inst.endswith("_USDC"):
            return inst
        return f"{inst}USDT"

    def _position_side_to_direction(self, position_side: str) -> str:
        ps = position_side.upper()
        if ps == "LONG":
            return "long"
        if ps == "SHORT":
            return "short"
        raise RuntimeError(f"Unsupported position side: {position_side}")

    def _normalize_position_side(self, direction: Any) -> str:
        value = str(direction or "").lower()
        if value == "long":
            return "LONG"
        if value == "short":
            return "SHORT"
        return "LONG"

    def _parse_position_size(self, row: dict[str, Any]) -> Decimal:
        # Prefer base-size quantity because our engine thinks in base asset units.
        candidates = [
            row.get("quantity"),
            row.get("baseSize"),
            row.get("currentPiece"),
            row.get("totalPiece"),
        ]
        for candidate in candidates:
            value = self._safe_decimal(candidate)
            if value > 0:
                return value
        return Decimal("0")

    def _order_error(self, order: OrderRequest, message: str) -> OrderResult:
        return OrderResult(
            success=False,
            exchange_order_id="",
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            position_side=order.position_side,
            status="rejected",
            requested_quantity=order.quantity,
            executed_quantity=Decimal("0"),
            price=order.price,
            average_price=None,
            raw=None,
            error_message=message,
        )
