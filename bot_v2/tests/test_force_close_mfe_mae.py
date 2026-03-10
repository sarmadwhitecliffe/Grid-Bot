from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bot_v2.bot import TradingBot
from bot_v2.models.enums import PositionSide
from bot_v2.models.position import Position


class DummyOrderManager:
    def __init__(self, filled, avg):
        self._filled = filled
        self._avg = avg

    async def create_market_order(self, *args, **kwargs):
        return {
            "filled": float(self._filled),
            "average": float(self._avg),
            "remaining": 0,
        }


@pytest.mark.asyncio
async def test_force_close_updates_mfe_mae(tmp_path, monkeypatch):
    # Create a minimal strategy config object
    cfg = SimpleNamespace(
        symbol_id="TEST/USDT",
        data_dir=str(tmp_path),
        mode="local_sim",
        initial_capital=100000,
    )
    bot = TradingBot(cfg)

    # Create a LONG position with peak above entry to ensure MFE
    entry_price = Decimal("100")
    initial_amount = Decimal("1")
    pos = Position(
        symbol_id="TEST/USDT",
        side=PositionSide.LONG,
        entry_price=entry_price,
        entry_time=datetime.now(timezone.utc),
        initial_amount=initial_amount,
        entry_atr=Decimal("1"),
        initial_risk_atr=Decimal("2"),
        total_entry_fee=Decimal("0"),
        soft_sl_price=Decimal("95"),
        hard_sl_price=Decimal("90"),
        tp1_price=Decimal("110"),
    )

    # Simulate peak favorable movement
    pos.peak_price_since_entry = Decimal("105")
    pos.mfe = Decimal("0")
    pos.mae = Decimal("0")

    bot.positions[pos.symbol_id] = pos

    # Patch order manager and capital manager
    async def dummy_update_capital(symbol, pnl):
        return None

    bot.capital_manager.update_capital = dummy_update_capital

    # Ensure _get_order_manager_for_symbol returns dummy that fills at price 102
    monkeypatch.setattr(
        bot,
        "_get_order_manager_for_symbol",
        lambda s: DummyOrderManager(filled=pos.current_amount, avg=102),
    )

    # Run force close
    await bot._force_close_position(pos, "TEST_FORCE")

    # Check last trade history has non-zero mfe_r / mae_r
    last = bot.trade_history[-1]
    assert last["mfe_r"] != 0
    # MAE should be non-zero only if exit adverse; here exit (102) > entry for LONG so mae remains 0
    assert last["mae_r"] == 0

    # Now simulate an adverse exit to generate MAE
    # Re-use the same symbol to keep a valid config in bot
    pos2 = Position(
        symbol_id="TEST/USDT",
        side=PositionSide.LONG,
        entry_price=Decimal("100"),
        entry_time=datetime.now(timezone.utc),
        initial_amount=Decimal("1"),
        entry_atr=Decimal("1"),
        initial_risk_atr=Decimal("2"),
        total_entry_fee=Decimal("0"),
        soft_sl_price=Decimal("95"),
        hard_sl_price=Decimal("90"),
        tp1_price=Decimal("110"),
    )
    pos2.peak_price_since_entry = Decimal("101")
    pos2.mfe = Decimal("0")
    pos2.mae = Decimal("0")
    bot.positions[pos2.symbol_id] = pos2
    monkeypatch.setattr(
        bot,
        "_get_order_manager_for_symbol",
        lambda s: DummyOrderManager(filled=pos2.current_amount, avg=95),
    )

    await bot._force_close_position(pos2, "ADVERSE_EXIT")
    last2 = bot.trade_history[-1]
    assert last2["mae_r"] != 0
