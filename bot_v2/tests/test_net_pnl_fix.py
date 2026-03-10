from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot_v2.bot import TradingBot
from bot_v2.models.exit_condition import ExitCondition
from bot_v2.models.position import Position, PositionSide
from bot_v2.models.strategy_config import StrategyConfig


@pytest.fixture
def mock_config():
    config = MagicMock(spec=StrategyConfig)
    config.data_dir = "test_data"
    config.symbols = ["BTCUSDT"]
    config.symbol_id = "BTCUSDT"
    config.exchange = "binance"
    config.mode = "local_sim"
    config.breakeven_offset_atr = Decimal("0.5")
    return config


@pytest.fixture
def bot(mock_config):
    bot = TradingBot(mock_config, simulation_mode=True)
    bot.capital_manager = AsyncMock()
    bot.capital_manager.update_capital = AsyncMock()
    bot._get_current_price = AsyncMock(return_value=Decimal("110.0"))
    bot._get_exchange_position = AsyncMock(return_value=Decimal("1.0"))
    bot._add_trade_to_history = MagicMock()
    bot._update_performance_metrics = MagicMock()
    bot._check_tier_transition = AsyncMock()
    bot._send_status_to_generator = AsyncMock()
    bot._send_exit_notification = AsyncMock()
    bot._get_current_atr = AsyncMock(return_value=Decimal("5.0"))
    bot.state_manager = MagicMock()
    return bot


@pytest.mark.asyncio
async def test_exit_position_net_pnl_calculation(bot):
    """
    Verify that _exit_position calculates Net PNL by deducting fees.

    Scenario:
    - Long Position 1.0 BTC @ $100
    - Exit @ $110
    - Gross PNL = (110 - 100) * 1.0 = $10.0
    - Entry Fee = $0.5
    - Exit Fee = $0.6
    - Expected Net PNL = 10.0 - 0.5 - 0.6 = $8.9
    """
    # Setup Position
    position = Position(
        symbol_id="BTCUSDT",
        side=PositionSide.LONG,
        entry_price=Decimal("100.0"),
        entry_time=datetime.now(timezone.utc),
        initial_amount=Decimal("1.0"),
        current_amount=Decimal("1.0"),
        entry_atr=Decimal("10.0"),
        initial_risk_atr=Decimal("10.0"),
        total_entry_fee=Decimal("0.5"),  # Entry Fee
        soft_sl_price=Decimal("90.0"),
        hard_sl_price=Decimal("80.0"),
        tp1_price=Decimal("120.0"),
    )
    bot.positions["BTCUSDT"] = position

    # Mock Order Manager Response
    mock_order_manager = AsyncMock()
    mock_order_manager.create_market_order.return_value = {
        "filled": "1.0",
        "average": "110.0",
        "remaining": "0.0",
        "status": "closed",
        "fee": {"cost": "0.6", "currency": "USDT"},  # Exit Fee
    }
    bot._get_order_manager_for_symbol = MagicMock(return_value=mock_order_manager)
    bot._get_config = MagicMock(return_value=mock_config)

    # Execute Exit
    await bot._exit_position(position, "TEST_EXIT")

    # Verify Capital Update
    # Expected Net PNL: 10.0 (Gross) - 0.5 (Entry) - 0.6 (Exit) = 8.9
    expected_net_pnl = Decimal("8.9")

    bot.capital_manager.update_capital.assert_called_once()
    args = bot.capital_manager.update_capital.call_args[0]
    symbol, pnl = args

    assert symbol == "BTCUSDT"
    assert isinstance(pnl, Decimal)
    assert abs(pnl - expected_net_pnl) < Decimal(
        "0.0001"
    ), f"Expected Net PNL {expected_net_pnl}, got {pnl}"


@pytest.mark.asyncio
async def test_partial_close_net_pnl_calculation(bot, mock_config):
    """
    Verify that _partial_close_position calculates Net PNL correctly.

    Scenario:
    - Long Position 1.0 BTC @ $100
    - Partial Close 0.5 BTC @ $110
    - Gross PNL = (110 - 100) * 0.5 = $5.0
    - Total Entry Fee = $0.5 (for 1.0 BTC) -> Proportional Share = 0.25
    - Exit Fee = $0.3
    - Expected Net PNL = 5.0 - 0.25 - 0.3 = $4.45
    """
    # Setup Position
    position = Position(
        symbol_id="BTCUSDT",
        side=PositionSide.LONG,
        entry_price=Decimal("100.0"),
        entry_time=datetime.now(timezone.utc),
        initial_amount=Decimal("1.0"),
        current_amount=Decimal("1.0"),
        entry_atr=Decimal("10.0"),
        initial_risk_atr=Decimal("10.0"),
        total_entry_fee=Decimal("0.5"),  # Total Entry Fee
        soft_sl_price=Decimal("90.0"),
        hard_sl_price=Decimal("80.0"),
        tp1_price=Decimal("120.0"),
    )
    bot.positions["BTCUSDT"] = position

    # Mock Order Manager Response
    mock_order_manager = AsyncMock()
    mock_order_manager.create_market_order.return_value = {
        "filled": "0.5",
        "average": "110.0",
        "remaining": "0.0",
        "status": "closed",
        "fee": {"cost": "0.3", "currency": "USDT"},  # Exit Fee
    }
    bot._get_order_manager_for_symbol = MagicMock(return_value=mock_order_manager)
    bot._get_config = MagicMock(return_value=mock_config)

    # Execute Partial Close
    exit_result = ExitCondition(
        reason="TP1a",
        priority=1,
        amount=Decimal("0.5"),
        price=Decimal("110.0"),
        message="Test Partial",
    )
    await bot._partial_close_position(position, exit_result)

    # Verify Capital Update
    # Expected Net PNL: 5.0 (Gross) - 0.25 (Entry Share) - 0.3 (Exit) = 4.45
    expected_net_pnl = Decimal("4.45")

    bot.capital_manager.update_capital.assert_called_once()
    args = bot.capital_manager.update_capital.call_args[0]
    symbol, pnl = args

    assert symbol == "BTCUSDT"
    assert isinstance(pnl, Decimal)
    assert abs(pnl - expected_net_pnl) < Decimal(
        "0.0001"
    ), f"Expected Net PNL {expected_net_pnl}, got {pnl}"
