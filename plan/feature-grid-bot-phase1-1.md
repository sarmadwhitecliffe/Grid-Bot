---
goal: 'Foundation & Data Ingestion — Project setup, configuration management, exchange connectivity, and OHLCV price feed'
version: '2.0'
date_created: '2026-02-22'
last_updated: '2026-02-22'
owner: 'Antigravity'
status: 'Complete'
tags: ['feature', 'foundation', 'data', 'standalone']
---

# Phase 1 — Foundation & Data Ingestion

![Status: Complete](https://img.shields.io/badge/status-Complete-brightgreen)

Phase 1 establishes the **bedrock of the standalone Grid Bot project**. It creates the project scaffold from scratch, implements the configuration management system (Pydantic + YAML), the low-level `ccxt` async exchange client for limit orders, and the unified price feed that provides both historical OHLCV data for indicators and a real-time update stream for execution.

> **This phase has zero dependencies on any pre-existing project.** All utilities are written fresh within the `grid_trading_bot/` directory.

---

## 1. Requirements & Constraints

| ID | Requirement |
|----|-------------|
| **REQ-1.1** | Use **Pydantic `BaseSettings`** for validated configuration loading from `.env` and `grid_config.yaml`. |
| **REQ-1.2** | `ccxt` integration must support both **Spot and Futures** modes, switchable via a single config flag. |
| **REQ-1.3** | Exchange client must implement: `place_limit_order`, `cancel_order`, `get_order_status`, `fetch_open_orders`, `fetch_ticker`. |
| **REQ-1.4** | Price feed must fetch **historical OHLCV** directly from the exchange REST API, with local Parquet caching to avoid redundant requests. |
| **REQ-1.5** | Price feed must provide a **real-time price update** mechanism (WebSocket via `ccxt.pro` if available, else REST polling). |
| **CON-1.1** | API credentials must only be loaded from `.env` — never hardcoded. |
| **CON-1.2** | All exchange calls must use `enableRateLimit=True`. Wrap all calls in `try/except` with exponential backoff retry (max 3 retries). |
| **CON-1.3** | Historical OHLCV must be cached locally as `.parquet` files in `data/cache/ohlcv_cache/` to reduce API calls on restart. |

---

## 2. Implementation Tasks

### GOAL-101: Project Scaffold

| Task | Description | Sprint | Has Tests | Done | Date |
|------|-------------|--------|-----------|------|------|
| TASK-101 | Create project root, all directories, `__init__.py` files, `.gitignore`, `.env.example` | Sprint 1 | ❌ | ✅ | 2026-02-22 |
| TASK-102 | Write `requirements.txt` with all pinned dependencies | Sprint 1 | ❌ | ✅ | 2026-02-22 |
| TASK-103 | Write `config/settings.py` (Pydantic BaseSettings + YAML loader) | Sprint 1 | ✅ | ✅ | 2026-02-22 |
| TASK-104 | Write `config/grid_config.yaml` with all default parameters | Sprint 1 | ❌ | ✅ | 2026-02-22 |
| TASK-105 | Write `src/exchange/exchange_client.py` (async ccxt wrapper) | Sprint 1 | ✅ | ✅ | 2026-02-22 |
| TASK-106 | Write `src/data/price_feed.py` (historical + real-time feed) | Sprint 1 | ✅ | ✅ | 2026-02-22 |
| TASK-107 | Write `run_grid_bot.sh` launcher script | Sprint 1 | ❌ | ✅ | 2026-02-22 |

---

## 3. Detailed Implementation Specifications

### 3.1 `config/settings.py` — Configuration System

**Purpose:** Single source of truth. Loads `.env` for secrets and merges with `grid_config.yaml` for strategy parameters.

```python
# config/settings.py
import yaml
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

CONFIG_DIR = Path(__file__).parent
PROJECT_ROOT = CONFIG_DIR.parent


def load_yaml_config() -> dict:
    """Load grid_config.yaml and return as a flat dict."""
    config_path = CONFIG_DIR / "grid_config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


class GridBotSettings(BaseSettings):
    """
    All configurable parameters for the Grid Bot.
    Precedence: ENV vars > grid_config.yaml defaults.
    """
    # --- Exchange ---
    EXCHANGE_ID: str = Field("binance", description="ccxt exchange ID, e.g. 'binance', 'bybit'")
    MARKET_TYPE: str = Field("spot", description="'spot' or 'futures'")
    API_KEY: str = Field(..., description="Exchange API Key — REQUIRED in .env")
    API_SECRET: str = Field(..., description="Exchange API Secret — REQUIRED in .env")
    TESTNET: bool = Field(False, description="Use exchange testnet/sandbox if True")
    SYMBOL: str = Field("BTC/USDT", description="Trading pair e.g. 'BTC/USDT', 'ETH/USDT'")

    # --- Grid ---
    GRID_TYPE: str = Field("geometric", description="'arithmetic' or 'geometric'")
    GRID_SPACING_PCT: float = Field(0.01, description="Decimal gap for geometric mode (0.01 = 1%)")
    GRID_SPACING_ABS: float = Field(50.0, description="Absolute $ gap for arithmetic mode")
    NUM_GRIDS_UP: int = Field(10, description="Sell levels above centre price")
    NUM_GRIDS_DOWN: int = Field(10, description="Buy levels below centre price")
    ORDER_SIZE_QUOTE: float = Field(100.0, description="USDT per grid level")
    LOWER_BOUND: float | None = Field(None, description="Hard lower price boundary")
    UPPER_BOUND: float | None = Field(None, description="Hard upper price boundary")

    # --- Capital ---
    TOTAL_CAPITAL: float = Field(2000.0, description="Total USDT allocated to bot")
    RESERVE_CAPITAL_PCT: float = Field(0.10, description="Fraction kept as reserve buffer")
    MAX_OPEN_ORDERS: int = Field(20, description="Hard cap on simultaneous open orders")

    # --- Risk ---
    STOP_LOSS_PCT: float = Field(0.05, description="Pause if price drops 5% below lower bound")
    MAX_DRAWDOWN_PCT: float = Field(0.15, description="Emergency close at 15% equity drop")
    TAKE_PROFIT_PCT: float = Field(0.30, description="Lock profits at 30% cumulative gain")
    ADX_THRESHOLD: int = Field(25, description="Pause bot if ADX exceeds this value")
    RECENTRE_TRIGGER: int = Field(3, description="Re-centre grid if price drifts > N levels")

    # --- Timing ---
    POLL_INTERVAL_SEC: int = Field(10, description="Seconds between REST polling cycles")
    OHLCV_TIMEFRAME: str = Field("1h", description="Candle timeframe for indicators")
    OHLCV_LIMIT: int = Field(200, description="Number of candles to fetch for indicator calculation")

    # --- Telegram ---
    TELEGRAM_BOT_TOKEN: str = Field("", description="Telegram Bot token from @BotFather")
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
        if v not in ("spot", "futures"):
            raise ValueError("MARKET_TYPE must be 'spot' or 'futures'")
        return v

    @field_validator("GRID_TYPE")
    @classmethod
    def validate_grid_type(cls, v: str) -> str:
        if v not in ("arithmetic", "geometric"):
            raise ValueError("GRID_TYPE must be 'arithmetic' or 'geometric'")
        return v

    model_config = {"env_file": str(PROJECT_ROOT / ".env"), "env_file_encoding": "utf-8"}


def get_settings() -> GridBotSettings:
    """Return a fully validated settings instance, merging YAML defaults with env overrides."""
    yaml_defaults = load_yaml_config()
    return GridBotSettings(**yaml_defaults)


# Singleton accessor
settings = get_settings()
```

**`.env.example`** template:
```dotenv
# Exchange
EXCHANGE_ID=binance
MARKET_TYPE=spot
API_KEY=your_api_key_here
API_SECRET=your_api_secret_here
TESTNET=false

# Telegram Alerts
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

---

### 3.2 `config/grid_config.yaml` — Default Parameter File

```yaml
# config/grid_config.yaml
# All parameters here can be overridden by environment variables.

SYMBOL: "BTC/USDT"
MARKET_TYPE: "spot"
GRID_TYPE: "geometric"
GRID_SPACING_PCT: 0.01       # 1% between each level
GRID_SPACING_ABS: 50.0       # $50 between each level (arithmetic mode)
NUM_GRIDS_UP: 10
NUM_GRIDS_DOWN: 10
ORDER_SIZE_QUOTE: 100.0      # $100 USDT per order
MAX_OPEN_ORDERS: 20
TOTAL_CAPITAL: 2000.0
RESERVE_CAPITAL_PCT: 0.10

STOP_LOSS_PCT: 0.05
MAX_DRAWDOWN_PCT: 0.15
TAKE_PROFIT_PCT: 0.30
ADX_THRESHOLD: 25
RECENTRE_TRIGGER: 3

POLL_INTERVAL_SEC: 10
OHLCV_TIMEFRAME: "1h"
OHLCV_LIMIT: 200
```

---

### 3.3 `src/exchange/exchange_client.py` — Exchange Client

**Purpose:** Wraps `ccxt` async API. Provides strongly-typed methods for all exchange interactions needed by the bot.

```python
# src/exchange/exchange_client.py
import asyncio
import logging
from typing import Optional
import ccxt.async_support as ccxt
from config.settings import GridBotSettings

logger = logging.getLogger(__name__)

RETRY_DELAYS = [1, 2, 5]   # Exponential backoff delays in seconds


class ExchangeClient:
    """
    Async ccxt wrapper for limit order operations.
    Supports Spot and Futures (linear perpetuals) markets.
    """

    def __init__(self, settings: GridBotSettings):
        exchange_class = getattr(ccxt, settings.EXCHANGE_ID)
        params = {
            "apiKey": settings.API_KEY,
            "secret": settings.API_SECRET,
            "enableRateLimit": True,
        }
        if settings.TESTNET:
            params["options"] = {"defaultType": settings.MARKET_TYPE, "testnet": True}
            params["urls"] = {"api": "https://testnet.binance.vision/api"}  # example for Binance

        if settings.MARKET_TYPE == "futures":
            params.setdefault("options", {})["defaultType"] = "future"

        self.exchange: ccxt.Exchange = exchange_class(params)
        self.symbol = settings.SYMBOL

    async def load_markets(self) -> None:
        """Must be called once before any trade operations to load market metadata."""
        await self._retry(self.exchange.load_markets)

    async def get_ticker(self) -> dict:
        """Returns latest ticker: {symbol, last, bid, ask, ...}"""
        return await self._retry(self.exchange.fetch_ticker, self.symbol)

    async def place_limit_order(self, side: str, price: float, amount: float) -> dict:
        """
        Place a limit order.
        Args:
            side: 'buy' or 'sell'
            price: Limit price in quote currency
            amount: Quantity in base currency
        Returns:
            ccxt order dict (contains 'id', 'status', 'price', 'amount')
        """
        logger.info(f"Placing {side} limit order: {amount} @ {price}")
        return await self._retry(
            self.exchange.create_limit_order, self.symbol, side, amount, price
        )

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel an open limit order by ID."""
        logger.info(f"Canceling order {order_id}")
        return await self._retry(self.exchange.cancel_order, order_id, self.symbol)

    async def get_order_status(self, order_id: str) -> dict:
        """Fetch current status of a specific order."""
        return await self._retry(self.exchange.fetch_order, order_id, self.symbol)

    async def fetch_open_orders(self) -> list[dict]:
        """Returns list of all currently open orders for the symbol."""
        return await self._retry(self.exchange.fetch_open_orders, self.symbol)

    async def fetch_balance(self) -> dict:
        """Returns account balance dict: {currency: {free: X, used: Y, total: Z}}"""
        return await self._retry(self.exchange.fetch_balance)

    async def fetch_ohlcv(self, timeframe: str = "1h", limit: int = 200) -> list[list]:
        """
        Fetch historical OHLCV candles.
        Returns: List of [timestamp_ms, open, high, low, close, volume]
        """
        return await self._retry(self.exchange.fetch_ohlcv, self.symbol, timeframe, limit=limit)

    async def close(self) -> None:
        """Gracefully close the exchange async session."""
        await self.exchange.close()

    async def _retry(self, func, *args, **kwargs):
        """Retry a coroutine with exponential backoff on network errors."""
        for attempt, delay in enumerate(RETRY_DELAYS + [None]):
            try:
                return await func(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
                if delay is None:
                    raise
                logger.warning(f"Attempt {attempt+1} failed ({e}). Retrying in {delay}s...")
                await asyncio.sleep(delay)
            except ccxt.ExchangeError as e:
                logger.error(f"Exchange error (non-retryable): {e}")
                raise
```

---

### 3.4 `src/data/price_feed.py` — Price Feed

**Purpose:** Fetches historical OHLCV (with Parquet caching) and provides a real-time price stream via polling.

```python
# src/data/price_feed.py
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from src.exchange.exchange_client import ExchangeClient
from config.settings import GridBotSettings

logger = logging.getLogger(__name__)

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class PriceFeed:
    """
    Provides historical OHLCV data (with disk caching) and a real-time
    last-price stream via periodic REST polling.
    """

    def __init__(self, client: ExchangeClient, settings: GridBotSettings):
        self.client = client
        self.settings = settings
        self.cache_dir = settings.OHLCV_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._current_price: float | None = None
        self._price_callbacks: list[callable] = []

    # ── Historical Data ──────────────────────────────────────────────

    async def get_ohlcv_dataframe(self) -> pd.DataFrame:
        """
        Returns a DataFrame of OHLCV candles for indicator calculation.
        Uses local Parquet cache if available and recent (< 1 candle old).
        """
        cache_path = self._cache_path()
        if self._cache_is_fresh(cache_path):
            logger.debug(f"Loading OHLCV from cache: {cache_path}")
            return pd.read_parquet(cache_path)

        logger.info("Fetching fresh OHLCV from exchange...")
        raw = await self.client.fetch_ohlcv(
            timeframe=self.settings.OHLCV_TIMEFRAME,
            limit=self.settings.OHLCV_LIMIT
        )
        df = pd.DataFrame(raw, columns=COLUMNS)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
        df.to_parquet(cache_path, index=False)
        logger.info(f"Cached {len(df)} candles to {cache_path}")
        return df

    # ── Real-Time Price ──────────────────────────────────────────────

    def register_price_callback(self, callback: callable) -> None:
        """Register a callback to be called on every new price update."""
        self._price_callbacks.append(callback)

    @property
    def current_price(self) -> float | None:
        return self._current_price

    async def start_real_time_polling(self) -> None:
        """
        Starts a polling loop that fetches the latest ticker price every
        POLL_INTERVAL_SEC seconds and notifies all registered callbacks.
        Run this as an asyncio task: asyncio.create_task(feed.start_real_time_polling())
        """
        logger.info(f"Starting real-time price polling every {self.settings.POLL_INTERVAL_SEC}s")
        while True:
            try:
                ticker = await self.client.get_ticker()
                new_price = float(ticker["last"])
                if new_price != self._current_price:
                    self._current_price = new_price
                    for cb in self._price_callbacks:
                        await cb(new_price)
            except Exception as e:
                logger.warning(f"Price polling error: {e}")
            await asyncio.sleep(self.settings.POLL_INTERVAL_SEC)

    # ── Helpers ──────────────────────────────────────────────────────

    def _cache_path(self) -> Path:
        safe_symbol = self.settings.SYMBOL.replace("/", "_")
        return self.cache_dir / f"{safe_symbol}_{self.settings.OHLCV_TIMEFRAME}.parquet"

    def _cache_is_fresh(self, path: Path) -> bool:
        """Returns True if cache file exists and was written within the last candle period."""
        if not path.exists():
            return False
        modified_ago_sec = (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime)
        # Map timeframe to seconds
        tf_seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
        candle_sec = tf_seconds.get(self.settings.OHLCV_TIMEFRAME, 3600)
        return modified_ago_sec < candle_sec
```

---

## 4. Alternatives Considered

| ID | Alternative | Decision |
|----|-------------|----------|
| **ALT-101** | Use WebSocket (`ccxt.pro`) for real-time price feed instead of REST polling | *Deferred to Phase 4 enhancement. REST polling is simpler, sufficient for 10-second cadence, and avoids WebSocket connection management complexity in Phase 1.* |
| **ALT-102** | Use SQLite for OHLCV caching | *Rejected: Parquet is faster for bulk DataFrame reads and requires no schema management.* |

---

## 5. Dependencies

```
# All installed via: pip install -r requirements.txt
ccxt>=4.2.0
pydantic>=2.0.0
pydantic-settings>=2.0.0
python-dotenv>=1.0.0
PyYAML>=6.0.0
pandas>=2.0.0
pyarrow>=14.0.0    # Parquet support
```

No dependencies on any other project directory.

---

## 6. Files Produced in This Phase

| File | Purpose |
|------|---------|
| `config/__init__.py` | Package marker |
| `config/settings.py` | Pydantic settings model |
| `config/grid_config.yaml` | Default parameter YAML |
| `src/__init__.py` | Package marker |
| `src/exchange/__init__.py` | Package marker |
| `src/exchange/exchange_client.py` | Async ccxt wrapper |
| `src/data/__init__.py` | Package marker |
| `src/data/price_feed.py` | OHLCV + real-time feed |
| `requirements.txt` | All project dependencies |
| `.env.example` | API key template |
| `.gitignore` | Excluding `.env`, logs, cache |
| `run_grid_bot.sh` | Bash launcher |

---

## 7. Testing

| Test ID | Description | File |
|---------|-------------|------|
| **TEST-101** | Pydantic validation catches invalid `MARKET_TYPE` and `GRID_TYPE` values | `tests/test_settings.py` |
| **TEST-102** | `ExchangeClient.place_limit_order` constructs the correct ccxt call (mocked exchange) | `tests/test_exchange_client.py` |
| **TEST-103** | `PriceFeed.get_ohlcv_dataframe` returns cached Parquet on second call (no API call) | `tests/test_price_feed.py` |
| **TEST-104** | `PriceFeed` real-time polling notifies registered callbacks with updated price | `tests/test_price_feed.py` |

---

## 8. Risks & Assumptions

| ID | Detail |
|----|--------|
| **RISK-1.1** | Exchange API rate-limit bans during high-frequency polling. *Mitigation: `enableRateLimit=True` + exponential backoff.* |
| **RISK-1.2** | Parquet cache is stale after long downtime. *Mitigation: freshness check compares file mtime vs. candle period.* |
| **ASSUMPTION-1.1** | User provides valid API keys with read + trade permissions. |
| **ASSUMPTION-1.2** | Python 3.11+ is available (`float | None` union syntax). |

---

## 9. Related Documents

- [Master Plan](./feature-grid-bot-master-1.md)
- [Phase 2 — Strategy Core](./feature-grid-bot-phase2-1.md)
