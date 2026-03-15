"""
config/settings.py
------------------
Single source of truth for all Grid Bot configuration.

Loads secrets from .env and merges with strategy parameters from
grid_config.yaml. Uses Pydantic BaseSettings for validation.

Precedence: ENV vars > grid_config.yaml defaults.
"""

import yaml
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

CONFIG_DIR = Path(__file__).parent
PROJECT_ROOT = CONFIG_DIR.parent


def load_yaml_config() -> dict:
    """
    Load grid_config.yaml and return as a flat dictionary.

    Returns:
        dict: YAML keys as a flat dictionary of parameter defaults.
    """
    config_path = CONFIG_DIR / "grid_config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f)


class GridBotSettings(BaseSettings):
    """
    All configurable parameters for the Grid Bot.

    Secrets (API keys, Telegram tokens) are loaded from .env.
    Any parameter can be overridden by an environment variable.
    """

    # --- Exchange ---
    EXCHANGE_ID: str = Field("binance", description="ccxt exchange ID")
    MARKET_TYPE: str = Field("spot", description="'spot' or 'futures'")
    API_KEY: str = Field("test_api_key", description="Exchange API Key")
    API_SECRET: str = Field("test_api_secret", description="Exchange API Secret")
    TESTNET: bool = Field(False, description="Use exchange testnet/sandbox if True")
    SYMBOL: str = Field("BTC/USDT", description="Trading pair")

    # --- Grid ---
    GRID_TYPE: str = Field("geometric", description="'arithmetic' or 'geometric'")
    GRID_SPACING_PCT: float = Field(0.01, description="Decimal gap for geometric mode")
    GRID_SPACING_ABS: float = Field(
        50.0, description="Absolute $ gap for arithmetic mode"
    )
    NUM_GRIDS_UP: int = Field(10, description="Sell levels above centre price")
    NUM_GRIDS_DOWN: int = Field(10, description="Buy levels below centre price")
    ORDER_SIZE_QUOTE: float = Field(100.0, description="USDT per grid level")
    LOWER_BOUND: Optional[float] = Field(None, description="Hard lower price boundary")
    UPPER_BOUND: Optional[float] = Field(None, description="Hard upper price boundary")
    DUAL_SIDE: bool = Field(True, description="Enable long/short dual-side grid")

    # --- Capital ---
    TOTAL_CAPITAL: float = Field(2000.0, description="Total USDT allocated to bot")
    RESERVE_CAPITAL_PCT: float = Field(
        0.10, description="Fraction kept as reserve buffer"
    )
    MAX_OPEN_ORDERS: int = Field(20, description="Hard cap on simultaneous open orders")
    LEVERAGE: int = Field(3, description="Futures leverage (1x-20x)")
    MARGIN_MODE: str = Field("isolated", description="'isolated' or 'cross'")

    # --- Risk ---
    STOP_LOSS_PCT: float = Field(
        0.05, description="Pause if price drops 5% below lower bound"
    )
    MAX_DRAWDOWN_PCT: float = Field(
        0.15, description="Emergency close at 15% equity drop"
    )
    TAKE_PROFIT_PCT: float = Field(
        0.30, description="Lock profits at 30% cumulative gain"
    )
    ADX_THRESHOLD: int = Field(25, description="Pause bot if ADX exceeds this value")
    bb_width_threshold: float = Field(
        0.04, description="Pause bot if BB width exceeds this value"
    )
    RECENTRE_TRIGGER: int = Field(3, description="Re-centre if price drifts > N levels")
    FUNDING_INTERVAL_HOURS: int = Field(8, description="Hours between funding payments")

    # --- Grid Sizing (v2) ---
    MIN_ORDER_SIZE_USD: float = Field(
        5.0, description="Minimum notional per order (exchange requirement)"
    )
    MAX_ORDER_SIZE_USD: float = Field(
        100.0,
        description="Maximum notional per order (cap to prevent oversized orders)",
    )
    MIN_GRID_LEVELS: int = Field(10, description="Minimum grid levels to maintain")

    # --- Timing ---
    POLL_INTERVAL_SEC: int = Field(
        10, description="Seconds between REST polling cycles"
    )
    OHLCV_TIMEFRAME: str = Field("1h", description="Candle timeframe for indicators")
    OHLCV_LIMIT: int = Field(200, description="Number of candles to fetch")

    # --- Telegram ---
    TELEGRAM_BOT_TOKEN: str = Field(
        "", description="Telegram Bot token from @BotFather"
    )
    TELEGRAM_CHAT_ID: str = Field("", description="Telegram chat ID for alerts")

    # --- Paths ---
    STATE_FILE: Path = Field(
        default_factory=lambda: PROJECT_ROOT / "data" / "state" / "grid_state.json"
    )
    LOG_FILE: Path = Field(
        default_factory=lambda: PROJECT_ROOT / "data" / "logs" / "grid_bot.log"
    )
    OHLCV_CACHE_DIR: Path = Field(
        default_factory=lambda: PROJECT_ROOT / "data" / "cache" / "ohlcv_cache"
    )

    @field_validator("MARKET_TYPE")
    @classmethod
    def validate_market_type(cls, v: str) -> str:
        """Ensure MARKET_TYPE is either 'spot' or 'futures'."""
        if v not in ("spot", "futures"):
            raise ValueError("MARKET_TYPE must be 'spot' or 'futures'")
        return v

    @field_validator("GRID_TYPE")
    @classmethod
    def validate_grid_type(cls, v: str) -> str:
        """Ensure GRID_TYPE is either 'arithmetic' or 'geometric'."""
        if v not in ("arithmetic", "geometric"):
            raise ValueError("GRID_TYPE must be 'arithmetic' or 'geometric'")
        return v

    model_config = {
        "env_file": str(PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


def get_settings() -> GridBotSettings:
    """
    Return a fully validated settings instance.

    Merges YAML defaults with environment variable overrides.

    Returns:
        GridBotSettings: Validated, fully populated settings object.
    """
    yaml_defaults = load_yaml_config()
    return GridBotSettings(**yaml_defaults)


# Module-level singleton — import this in all other modules.
settings = get_settings()
