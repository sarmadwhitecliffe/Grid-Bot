"""
Test configuration and fixtures for pytest.

This module provides shared fixtures and test utilities for all test modules.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_v2.models.enums import PositionSide, PositionStatus
from bot_v2.models.position import Position
from bot_v2.models.strategy_config import StrategyConfig


@pytest.fixture
def temp_dir(tmp_path):
    """Provide temporary directory for tests."""
    return tmp_path


@pytest.fixture
def temp_data_dir(tmp_path):
    """Provide temporary data directory (string path) for tests."""
    return str(tmp_path)


@pytest.fixture
def sample_capital_data():
    """Sample capital data for testing."""
    return {
        "BTCUSDT": {"capital": "1000.00", "mode": "simulation", "tier": "PROBATION"},
        "ETHUSDT": {"capital": "500.00", "mode": "live", "tier": "CONSERVATIVE"},
    }


@pytest.fixture
def sample_position_data():
    """Sample position data for testing."""
    return {
        "symbol": "BTCUSDT",
        "side": "long",
        "entry_price": "50000.00",
        "position_size": "0.02",
        "leverage": "5",
        "execution_mode": "simulation",
        "entry_time": "2025-11-01T10:00:00Z",
        "tier": "PROBATION",
    }


@pytest.fixture
def sample_long_position():
    """Create a sample LONG position for testing."""
    return Position(
        symbol_id="BTCUSDT",
        side=PositionSide.LONG,
        entry_price=Decimal("100.0"),
        initial_amount=Decimal("1.0"),
        current_amount=Decimal("1.0"),
        entry_atr=Decimal("2.0"),
        initial_risk_atr=Decimal("2.0"),
        total_entry_fee=Decimal("0.01"),
        soft_sl_price=Decimal("95.0"),
        hard_sl_price=Decimal("93.0"),
        tp1_price=Decimal("110.0"),
        entry_time=datetime.now(timezone.utc),
        status=PositionStatus.OPEN,
        current_r=Decimal("0.0"),
        peak_price_since_entry=Decimal("100.0"),
        mfe=Decimal("0.0"),
        mae=Decimal("0.0"),
    )


@pytest.fixture
def sample_short_position():
    """Create a sample SHORT position for testing."""
    return Position(
        symbol_id="BTCUSDT",
        side=PositionSide.SHORT,
        entry_price=Decimal("100.0"),
        initial_amount=Decimal("1.0"),
        current_amount=Decimal("1.0"),
        entry_atr=Decimal("2.0"),
        initial_risk_atr=Decimal("2.0"),
        total_entry_fee=Decimal("0.01"),
        soft_sl_price=Decimal("105.0"),
        hard_sl_price=Decimal("107.0"),
        tp1_price=Decimal("90.0"),
        entry_time=datetime.now(timezone.utc),
        status=PositionStatus.OPEN,
        current_r=Decimal("0.0"),
        peak_price_since_entry=Decimal("100.0"),
        mfe=Decimal("0.0"),
        mae=Decimal("0.0"),
    )


@pytest.fixture
def sample_strategy():
    """Create a sample strategy config for testing."""
    return StrategyConfig(
        symbol_id="BTCUSDT",
        timeframe="15m",
        trail_sl_atr_mult=Decimal("2.0"),
        soft_sl_atr_mult=Decimal("1.5"),
        hard_sl_atr_mult=Decimal("3.0"),
        catastrophic_stop_mult=Decimal("4.0"),
        tp1_atr_mult=Decimal("1.2"),
        ape_min_r=Decimal("0.3"),
        ape_pullback_pct=Decimal("0.15"),
        ape_min_ratio=Decimal("5.0"),
        min_bars_before_mfe_mae_cut=5,
        mae_persist_bars=3,
    )


@pytest.fixture
def mock_config():
    """Mock bot configuration."""
    return {
        "BTCUSDT": {
            "mode": "local_sim",
            "leverage": 5,
            "capital_usage_percent": 100,
            "timeframe": "5m",
        }
    }


def create_temp_json_file(temp_dir: Path, filename: str, data: dict) -> Path:
    """Helper to create a temporary JSON file."""
    file_path = temp_dir / filename
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)
    return file_path


@pytest.fixture(autouse=True)
def protect_strategy_configs():
    """Ensure tests never modify the real production strategy configs file.

    Captures the file contents (if present) before each test and asserts they
    are unchanged afterwards. Tests that need custom configs must use a temp
    directory and monkeypatch paths instead of writing to `config/strategy_configs.json`.
    """
    path = Path("config/strategy_configs.json")
    original_exists = path.exists()
    original_content = path.read_text() if original_exists else None
    yield
    # After test
    if original_exists != path.exists():
        assert False, "Tests must not create/delete production strategy_configs.json"
    if original_exists and path.read_text() != original_content:
        assert False, "Tests must not modify production strategy_configs.json contents"


# Support running asyncio-marked tests without adding pytest-asyncio dependency
import asyncio  # noqa: E402
import inspect


def pytest_pyfunc_call(pyfuncitem):
    """Execute async test functions in this test package when pytest-asyncio
    is not available. Only run for tests inside `bot_v2/` to avoid
    interfering with other packages that rely on pytest-asyncio's loop.
    """
    # Only handle tests under bot_v2 tests to avoid conflicting with other plugins
    try:
        fpath = str(pyfuncitem.fspath)
    except Exception:
        return None
    if "bot_v2" not in fpath:
        return None

    testfunction = pyfuncitem.obj
    if asyncio.iscoroutinefunction(testfunction):
        sig = inspect.signature(testfunction)
        allowed = set(sig.parameters.keys())
        kwargs = {k: v for k, v in pyfuncitem.funcargs.items() if k in allowed}

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(testfunction(**kwargs))
            return True
        finally:
            # Cancel any pending background tasks scheduled by the test or code under test
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                if pending:
                    for t in pending:
                        t.cancel()
                    try:
                        loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                    except Exception:
                        pass
            except RuntimeError:
                pass
            finally:
                loop.close()
