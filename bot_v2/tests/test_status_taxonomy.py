from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot_v2.bot import TradingBot
from bot_v2.models.position import PositionSide


@pytest.mark.asyncio
async def test_status_emission_taxonomy():
    """
    Test that POSITION_ENTERED is sent with correct payload including identifiers.
    """
    # Mock dependencies
    mock_exchange = AsyncMock()
    mock_exchange.get_market_price.return_value = Decimal("1.50")

    # We need to mock the dataframe returned by fetch_ohlcv
    import pandas as pd

    df = pd.DataFrame(
        {"high": [1.6, 1.6, 1.6], "low": [1.4, 1.4, 1.4], "close": [1.5, 1.5, 1.5]}
    )
    # fetch_ohlcv returns just the dataframe
    mock_exchange.fetch_ohlcv.return_value = df

    mock_order_manager = AsyncMock()
    # Return valid order structure
    mock_order_manager.create_market_order.return_value = {
        "id": "order_123",
        "status": "filled",
        "average": 1.50,
        "filled": 10.0,
        "fee": {"cost": 0.1},
    }

    # Mock config
    mock_config = MagicMock()
    mock_config.symbol_id = "IMX/USDT"
    mock_config.mode = "live"
    mock_config.exchange_name = "binance"
    mock_config.api_key = "key"
    mock_config.api_secret = "secret"
    mock_config.soft_sl_atr_mult = Decimal("1.0")
    mock_config.hard_sl_atr_mult = Decimal("2.0")
    mock_config.tp1_atr_mult = Decimal("3.0")
    mock_config.tp1a_atr_mult = Decimal("1.5")
    mock_config.atr_period = 3  # Match df length
    mock_config.timeframe = "1h"

    # Initialize bot with mocks
    # We need to patch LiveExchange so it doesn't try to connect during init
    with patch("bot_v2.bot.LiveExchange", return_value=mock_exchange):
        bot = TradingBot(config=mock_config)

    # Override the exchange used by the bot
    bot.live_exchange = mock_exchange

    # Override order manager
    bot.live_order_manager = mock_order_manager

    bot._send_status_to_generator = AsyncMock()
    bot._send_entry_notification = AsyncMock()

    # Fix capital_manager mocking
    bot.capital_manager = MagicMock()
    bot.capital_manager.get_capital = AsyncMock(return_value=Decimal("1000"))
    bot.capital_manager.apply_second_trade_override.return_value = Decimal("1")

    bot.risk_manager = AsyncMock()
    bot.risk_manager.calculate_position_params.return_value = {
        "allowed": True,
        "tier": "TIER_1",
        "capital_allocation_pct": 10.0,  # Increased to pass min notional check
        "leverage": 1,
    }
    bot.cost_filter = MagicMock()
    bot.cost_filter.is_cost_floor_met.return_value = True
    bot.volatility_filter = AsyncMock()
    bot.volatility_filter.is_volatile_enough.return_value = True

    # Mock config
    bot.strategy_configs = {"IMX/USDT": mock_config}

    # Simulate entry signal
    symbol = "IMX/USDT"
    side = PositionSide.LONG

    # Execute
    await bot._handle_entry_signal(symbol=symbol, side=side, metadata={}, tracker=None)

    # Verify call
    bot._send_status_to_generator.assert_called_once()

    # Verify payload
    call_args = bot._send_status_to_generator.call_args
    assert call_args is not None

    # _send_status_to_generator(symbol, status, extra_payload=...)
    args, kwargs = call_args
    symbol_arg = args[0]
    status_arg = args[1]

    # Check kwargs for extra_payload
    assert "extra_payload" in kwargs
    payload_arg = kwargs["extra_payload"]

    assert symbol_arg == "IMX/USDT"
    assert status_arg == "POSITION_ENTERED"

    assert "position_id" in payload_arg
    assert "entry_order_id" in payload_arg
    assert payload_arg["entry_order_id"] == "order_123"
    assert payload_arg["side"] == "long"
