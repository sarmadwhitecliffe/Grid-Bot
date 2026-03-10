from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bot_v2.bot import TradingBot
from bot_v2.models.enums import PositionSide, PositionStatus
from bot_v2.models.position import Position


@pytest.mark.asyncio
async def test_partial_close_skipped_marks_tp1a(monkeypatch, tmp_path):
    # Minimal strategy config stub
    class DummyCfg:
        symbol_id = "HYPE/USDT"
        mode = "local_sim"
        breakeven_offset_atr = Decimal("0.1")

    cfg = DummyCfg()
    # Ensure persistence writes go to a temp directory so repo state isn't modified by tests
    cfg.data_dir = tmp_path
    bot = TradingBot(cfg, simulation_mode=True)

    # Create a small position that will trigger notional < min limit
    position = Position(
        symbol_id="HYPE/USDT",
        side=PositionSide.LONG,
        entry_price=Decimal("26.59"),
        initial_amount=Decimal("1.1"),
        current_amount=Decimal("1.1"),
        entry_time=datetime.now(timezone.utc),
        status=PositionStatus.OPEN,
        entry_atr=Decimal("0.5"),
        initial_risk_atr=Decimal("0.5"),
        total_entry_fee=Decimal("0"),
        soft_sl_price=Decimal("0"),
        hard_sl_price=Decimal("0"),
        tp1_price=Decimal("0"),
    )

    # Exit result for TP1a (30% of initial amount)
    mock_exit_result = SimpleNamespace()
    mock_exit_result.amount = Decimal("0.33")  # small amount
    mock_exit_result.name = "TP1a"
    mock_exit_result.reason = "TP1a"

    # Patch methods
    monkeypatch.setenv("BOT_MIN_NOTIONAL_USD", "10.0")
    bot._get_config = lambda symbol: cfg
    from unittest.mock import AsyncMock

    bot._get_current_price = AsyncMock(return_value=Decimal("26.59"))

    mock_order_manager = AsyncMock()
    mock_order_manager.create_market_order = AsyncMock(
        side_effect=RuntimeError("Order should not be created when below min notional")
    )
    bot._get_order_manager_for_symbol = lambda symbol: mock_order_manager

    # Spy on capital update
    bot.capital_manager.update_capital = AsyncMock()

    # Run partial close
    await bot._partial_close_position(position, mock_exit_result)

    # Verify position saved with tp1a_hit=True and status PARTIALLY_CLOSED
    saved = bot.positions.get(position.symbol_id)
    assert saved is not None
    assert saved.tp1a_hit is True
    assert saved.status == PositionStatus.PARTIALLY_CLOSED
    # Ensure order manager was not called
    mock_order_manager.create_market_order.assert_not_called()
    bot.capital_manager.update_capital.assert_not_called()
