import os
from dataclasses import dataclass, field
from typing import List


def _get_env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and (value is None or str(value).strip() == ""):
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value if value is not None else ""


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer for env var {name}: {value}") from exc


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid float for env var {name}: {value}") from exc


def _get_list(name: str, default: List[str]) -> List[str]:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(slots=True)
class AppConfig:
    app_name: str = "coinw-trading-bot"
    env: str = field(default_factory=lambda: _get_env("APP_ENV", "development"))
    debug: bool = field(default_factory=lambda: _get_bool("DEBUG", False))
    log_level: str = field(default_factory=lambda: _get_env("LOG_LEVEL", "INFO").upper())

    timezone: str = field(default_factory=lambda: _get_env("TIMEZONE", "UTC"))
    mode: str = field(default_factory=lambda: _get_env("MODE", "paper").lower())

    # Telegram
    telegram_bot_token: str = field(default_factory=lambda: _get_env("TELEGRAM_BOT_TOKEN", required=True))
    telegram_admin_ids: List[int] = field(
        default_factory=lambda: [
            int(x) for x in _get_list("TELEGRAM_ADMIN_IDS", [])
            if str(x).strip().isdigit()
        ]
    )

    # Database
    mongo_uri: str = field(default_factory=lambda: _get_env("MONGO_URI", required=True))
    mongo_db_name: str = field(default_factory=lambda: _get_env("MONGO_DB_NAME", "coinw_trading_bot"))

    # Exchange
    exchange_name: str = field(default_factory=lambda: _get_env("EXCHANGE_NAME", "coinw").lower())
    coinw_rest_base_url: str = field(default_factory=lambda: _get_env("COINW_REST_BASE_URL", "https://api.coinw.com"))
    coinw_timeout_seconds: int = field(default_factory=lambda: _get_int("COINW_TIMEOUT_SECONDS", 15))
    coinw_recv_window_ms: int = field(default_factory=lambda: _get_int("COINW_RECV_WINDOW_MS", 5000))

    # Trading core
    default_quote_asset: str = field(default_factory=lambda: _get_env("DEFAULT_QUOTE_ASSET", "USDT").upper())
    symbols: List[str] = field(
        default_factory=lambda: _get_list(
            "SYMBOLS",
            ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        )
    )
    scan_interval_seconds: int = field(default_factory=lambda: _get_int("SCAN_INTERVAL_SECONDS", 20))
    position_check_interval_seconds: int = field(default_factory=lambda: _get_int("POSITION_CHECK_INTERVAL_SECONDS", 10))
    candle_limit: int = field(default_factory=lambda: _get_int("CANDLE_LIMIT", 300))
    timeframe: str = field(default_factory=lambda: _get_env("TIMEFRAME", "15m"))

    # Risk defaults
    default_leverage: int = field(default_factory=lambda: _get_int("DEFAULT_LEVERAGE", 3))
    risk_per_trade: float = field(default_factory=lambda: _get_float("RISK_PER_TRADE", 0.01))
    max_open_positions_per_user: int = field(default_factory=lambda: _get_int("MAX_OPEN_POSITIONS_PER_USER", 3))
    max_daily_loss_pct: float = field(default_factory=lambda: _get_float("MAX_DAILY_LOSS_PCT", 5.0))
    cooldown_after_loss_minutes: int = field(default_factory=lambda: _get_int("COOLDOWN_AFTER_LOSS_MINUTES", 15))

    # Execution safety
    dry_run: bool = field(default_factory=lambda: _get_bool("DRY_RUN", True))
    enable_order_execution: bool = field(default_factory=lambda: _get_bool("ENABLE_ORDER_EXECUTION", False))
    confirm_position_after_order: bool = field(default_factory=lambda: _get_bool("CONFIRM_POSITION_AFTER_ORDER", True))
    max_order_retries: int = field(default_factory=lambda: _get_int("MAX_ORDER_RETRIES", 3))
    retry_backoff_seconds: float = field(default_factory=lambda: _get_float("RETRY_BACKOFF_SECONDS", 1.5))

    # Future billing hooks (not active yet)
    billing_enabled: bool = field(default_factory=lambda: _get_bool("BILLING_ENABLED", False))
    fee_threshold_usdt: float = field(default_factory=lambda: _get_float("FEE_THRESHOLD_USDT", 5.0))

    def validate(self) -> None:
        allowed_modes = {"paper", "live"}
        if self.mode not in allowed_modes:
            raise RuntimeError(f"MODE must be one of {allowed_modes}, got: {self.mode}")

        allowed_exchanges = {"coinw"}
        if self.exchange_name not in allowed_exchanges:
            raise RuntimeError(f"EXCHANGE_NAME must be one of {allowed_exchanges}, got: {self.exchange_name}")

        if not self.symbols:
            raise RuntimeError("SYMBOLS cannot be empty")

        if self.default_leverage <= 0:
            raise RuntimeError("DEFAULT_LEVERAGE must be > 0")

        if self.risk_per_trade <= 0 or self.risk_per_trade > 1:
            raise RuntimeError("RISK_PER_TRADE must be > 0 and <= 1")

        if self.max_open_positions_per_user <= 0:
            raise RuntimeError("MAX_OPEN_POSITIONS_PER_USER must be > 0")

        if self.scan_interval_seconds <= 0:
            raise RuntimeError("SCAN_INTERVAL_SECONDS must be > 0")

        if self.position_check_interval_seconds <= 0:
            raise RuntimeError("POSITION_CHECK_INTERVAL_SECONDS must be > 0")

        if self.candle_limit < 50:
            raise RuntimeError("CANDLE_LIMIT should be at least 50")

        if self.coinw_timeout_seconds <= 0:
            raise RuntimeError("COINW_TIMEOUT_SECONDS must be > 0")

        if self.max_order_retries < 0:
            raise RuntimeError("MAX_ORDER_RETRIES cannot be negative")

        if self.retry_backoff_seconds <= 0:
            raise RuntimeError("RETRY_BACKOFF_SECONDS must be > 0")

        if self.mode == "live" and not self.enable_order_execution:
            raise RuntimeError(
                "Unsafe configuration: MODE=live but ENABLE_ORDER_EXECUTION is false. "
                "Set ENABLE_ORDER_EXECUTION=true only when you are ready."
            )

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"


config = AppConfig()
config.validate()
