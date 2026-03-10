from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from bot_v2.bot import TradingBot as Bot
from bot_v2.execution.live_exchange import LiveExchange
from bot_v2.models.enums import PositionSide


@pytest.mark.asyncio
async def test_leverage_cache_hit():
    """
    Verify that set_leverage is skipped if the leverage is already cached.
    """
    # Setup config with string keys to pass validation
    config_mock = {"BTC/USDT": MagicMock(), "ETH/USDT": MagicMock()}
    config_mock["BTC/USDT"].mode = "live"
    config_mock["BTC/USDT"].exchange_name = "binance"
    config_mock["BTC/USDT"].api_key = "key"
    config_mock["BTC/USDT"].api_secret = "secret"
    config_mock["BTC/USDT"].timeframe = "1h"
    config_mock["BTC/USDT"].atr_period = 14
    config_mock["BTC/USDT"].slippage_pct = Decimal("0.1")
    config_mock["BTC/USDT"].tp1_atr_mult = Decimal("1.0")
    config_mock["BTC/USDT"].cost_floor_multiplier = Decimal("1.0")

    # Mock ccxt_async to avoid actual network calls or import errors
    with patch("bot_v2.execution.live_exchange.ccxt_async") as mock_ccxt:
        mock_ccxt.binance = MagicMock()
        bot = Bot(config=config_mock)

    # Use LiveExchange instance to pass isinstance check
    bot.live_exchange = LiveExchange("binance", "key", "secret", MagicMock())
    bot.live_exchange.exchange = AsyncMock()  # Mock internal CCXT exchange
    bot.exchange = bot.live_exchange  # For direct access in test

    bot.risk_manager = MagicMock()
    bot.risk_manager.calculate_position_params = AsyncMock(
        return_value={
            "position_size": 1.0,
            "leverage": 10,
            "stop_loss": 49000.0,
            "take_profit": 51000.0,
            "allowed": True,
            "tier": "Tier 1",
            "capital_allocation_pct": 0.1,
        }
    )
    bot.capital_manager = MagicMock()
    bot.capital_manager.get_capital = AsyncMock(return_value=Decimal("1000.0"))
    bot.capital_manager.apply_second_trade_override.return_value = Decimal("10.0")
    bot.position_manager = MagicMock()

    # Setup mocks
    bot.risk_manager.get_leverage_for_symbol.return_value = 10
    bot.capital_manager.calculate_position_size.return_value = (1.0, 100.0)
    bot.live_exchange.exchange.create_market_order.return_value = {
        "id": "123",
        "status": "closed",
    }
    bot.live_exchange.get_market_price = AsyncMock(
        return_value=Decimal("50000.0")
    )  # Return Decimal

    # Mock OHLCV for ATR calculation
    df = pd.DataFrame(
        {"high": [51000.0] * 20, "low": [49000.0] * 20, "close": [50000.0] * 20}
    )
    bot.live_exchange.fetch_ohlcv = AsyncMock(return_value=df)

    # Pre-populate cache
    bot._leverage_cache["BTC/USDT"] = 10

    signal = {"symbol": "BTC/USDT", "action": "buy", "source": "dts"}

    # Run entry handler directly
    await bot._handle_entry_signal(signal["symbol"], PositionSide.LONG, metadata=signal)

    # Verify set_leverage was NOT called on the internal CCXT exchange
    bot.live_exchange.exchange.set_leverage.assert_not_called()


@pytest.mark.asyncio
async def test_leverage_cache_miss():
    """
    Verify that set_leverage is called if the leverage is NOT cached or different.
    """
    # Setup config with string keys to pass validation
    config_mock = {"BTC/USDT": MagicMock(), "ETH/USDT": MagicMock()}
    config_mock["BTC/USDT"].mode = "live"
    config_mock["BTC/USDT"].exchange_name = "binance"
    config_mock["BTC/USDT"].api_key = "key"
    config_mock["BTC/USDT"].api_secret = "secret"
    config_mock["BTC/USDT"].timeframe = "1h"
    config_mock["BTC/USDT"].atr_period = 14
    config_mock["BTC/USDT"].slippage_pct = Decimal("0.1")
    config_mock["BTC/USDT"].tp1_atr_mult = Decimal("1.0")
    config_mock["BTC/USDT"].cost_floor_multiplier = Decimal("1.0")

    # Mock ccxt_async to avoid actual network calls or import errors
    with patch("bot_v2.execution.live_exchange.ccxt_async") as mock_ccxt:
        mock_ccxt.binance = MagicMock()
        bot = Bot(config=config_mock)

    # Use LiveExchange instance to pass isinstance check
    bot.live_exchange = LiveExchange("binance", "key", "secret", MagicMock())
    bot.live_exchange.exchange = AsyncMock()  # Mock internal CCXT exchange
    bot.exchange = bot.live_exchange  # For direct access in test

    bot.risk_manager = MagicMock()
    bot.risk_manager.calculate_position_params = AsyncMock(
        return_value={
            "position_size": 1.0,
            "leverage": 20,
            "stop_loss": 49000.0,
            "take_profit": 51000.0,
            "allowed": True,
            "tier": "Tier 1",
            "capital_allocation_pct": 0.1,
        }
    )
    bot.capital_manager = MagicMock()
    bot.capital_manager.get_capital = AsyncMock(return_value=Decimal("1000.0"))
    bot.capital_manager.apply_second_trade_override.return_value = Decimal("20.0")
    bot.position_manager = MagicMock()

    # Setup mocks
    bot.risk_manager.get_leverage_for_symbol.return_value = 20
    bot.capital_manager.calculate_position_size.return_value = (1.0, 100.0)
    bot.live_exchange.exchange.create_market_order.return_value = {
        "id": "123",
        "status": "closed",
    }
    bot.live_exchange.get_market_price = AsyncMock(
        return_value=Decimal("50000.0")
    )  # Return Decimal

    # Mock OHLCV for ATR calculation
    df = pd.DataFrame(
        {"high": [51000.0] * 20, "low": [49000.0] * 20, "close": [50000.0] * 20}
    )
    bot.live_exchange.fetch_ohlcv = AsyncMock(return_value=df)

    # Cache has old value
    bot._leverage_cache["BTC/USDT"] = 10

    signal = {"symbol": "BTC/USDT", "action": "buy", "source": "dts"}

    # Run entry handler directly
    await bot._handle_entry_signal(signal["symbol"], PositionSide.LONG, metadata=signal)

    # Verify set_leverage WAS called with new value on internal CCXT exchange
    bot.live_exchange.exchange.set_leverage.assert_called_with(20, "BTC/USDT")

    # Assert cache updated
    assert bot._leverage_cache["BTC/USDT"] == 20
