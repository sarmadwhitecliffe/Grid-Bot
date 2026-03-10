"""
tests/conftest.py
------------------
Shared pytest fixtures for all Grid Bot test modules.

Provides:
  - A base GridBotSettings instance loaded from YAML defaults with API
    credentials replaced by safe dummy values.
  - Factory fixtures for constructing common objects (exchange, stores, etc.).
"""

import os
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from config.settings import GridBotSettings


# ---------------------------------------------------------------------------
# Base settings fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def base_settings() -> GridBotSettings:
    """
    Return a GridBotSettings instance loaded from YAML defaults with
    dummy API credentials so tests never need a real .env file.
    """
    # Load YAML defaults.
    from config.settings import load_yaml_config

    yaml_defaults = load_yaml_config()
    overrides = {
        "EXCHANGE_ID": "binance",
        "MARKET_TYPE": "spot",
        "API_KEY": "test_key",
        "API_SECRET": "test_secret",
        "SYMBOL": "BTC/USDT",
        "OHLCV_TIMEFRAME": "1h",
        "STATE_FILE": Path("/tmp/test_grid_state.json"),
        "LOG_FILE": Path("/tmp/grid_bot.log"),
        "OHLCV_CACHE_DIR": Path("/tmp/ohlcv_cache"),
        "LOWER_BOUND": 20000.0,
        "UPPER_BOUND": 40000.0,
        "GRID_SPACING_PCT": 0.01,
        "GRID_SPACING_ABS": 50.0,
        "RECENTRE_TRIGGER": 3,
        "TAKE_PROFIT_PCT": 0.3,
        "STOP_LOSS_PCT": 0.05,
        "MAX_DRAWDOWN_PCT": 0.15,
        "ADX_THRESHOLD": 25,
    }
    return GridBotSettings(**{**yaml_defaults, **overrides})


# ---------------------------------------------------------------------------
# OHLCV DataFrame fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """
    Return a 200-row synthetic OHLCV DataFrame with realistic BTC-like prices.

    Columns: timestamp, open, high, low, close, volume.
    Prices oscillate between ~29 000 and 31 000 to simulate a ranging market.
    """
    import numpy as np

    rng = np.random.default_rng(seed=42)
    n = 200
    timestamps = pd.date_range("2024-01-01", periods=n, freq="1h")
    base = 30_000.0
    closes = base + rng.normal(0, 300, n).cumsum() * 0.05
    highs = closes + rng.uniform(20, 150, n)
    lows = closes - rng.uniform(20, 150, n)
    opens = closes + rng.normal(0, 30, n)
    volumes = rng.uniform(100, 1000, n)

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


# ---------------------------------------------------------------------------
# Mock exchange fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_exchange() -> MagicMock:
    """Return a MagicMock stand-in for ExchangeClient with async methods."""
    exchange = MagicMock()
    exchange.place_limit_order = AsyncMock(
        return_value={"id": "order-001", "status": "open"}
    )
    exchange.cancel_order = AsyncMock(return_value={"status": "canceled"})
    exchange.get_order_status = AsyncMock(return_value={"status": "open", "filled": 0})
    exchange.fetch_open_orders = AsyncMock(return_value=[])
    exchange.fetch_balance = AsyncMock(
        return_value={"USDT": {"free": 1000.0, "total": 1000.0}}
    )
    exchange.get_ticker = AsyncMock(return_value={"last": 30_000.0})
    return exchange
