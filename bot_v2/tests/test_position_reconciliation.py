import asyncio
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock
from bot_v2.bot import TradingBot
from bot_v2.models.position import Position

@pytest.mark.asyncio
async def test_reconcile_no_live_exchange(mocker):
    bot = TradingBot.__new__(TradingBot)
    bot.live_exchange = None
    bot.positions = {"BTCUSDT": MagicMock()}
    
    await bot._reconcile_positions_with_exchange()
    # Should return immediately without error
    assert "BTCUSDT" in bot.positions

@pytest.mark.asyncio
async def test_reconcile_ok(mocker):
    bot = TradingBot.__new__(TradingBot)
    bot.live_exchange = MagicMock()
    bot.live_exchange.get_position_amount = AsyncMock(return_value=Decimal("1.0"))
    
    pos = MagicMock(spec=Position)
    pos.current_amount = Decimal("1.0")
    bot.positions = {"BTCUSDT": pos}
    
    bot.strategy_configs = {"BTCUSDT": MagicMock(mode="live")}
    bot.notifier = AsyncMock()
    bot.state_manager = MagicMock()
    
    await bot._reconcile_positions_with_exchange()
    
    assert "BTCUSDT" in bot.positions
    bot.notifier.send.assert_not_called()
    bot.state_manager.save_positions.assert_not_called()

@pytest.mark.asyncio
async def test_reconcile_ghost_position(mocker):
    bot = TradingBot.__new__(TradingBot)
    bot.live_exchange = MagicMock()
    # Exchange says 0
    bot.live_exchange.get_position_amount = AsyncMock(return_value=Decimal("0"))
    
    pos = MagicMock(spec=Position)
    pos.current_amount = Decimal("1.0")
    bot.positions = {"BTCUSDT": pos}
    
    bot.strategy_configs = {"BTCUSDT": MagicMock(mode="live")}
    bot.notifier = AsyncMock()
    bot.state_manager = MagicMock()
    
    await bot._reconcile_positions_with_exchange()
    
    assert "BTCUSDT" not in bot.positions
    bot.notifier.send.assert_called_once()
    assert "GHOST POSITION" in bot.notifier.send.call_args[0][0]
    bot.state_manager.save_positions.assert_called_once()

@pytest.mark.asyncio
async def test_reconcile_amount_mismatch(mocker):
    bot = TradingBot.__new__(TradingBot)
    bot.live_exchange = MagicMock()
    # Exchange says 0.5
    bot.live_exchange.get_position_amount = AsyncMock(return_value=Decimal("0.5"))
    
    pos = MagicMock(spec=Position)
    pos.current_amount = Decimal("1.0")
    pos.copy.return_value = MagicMock(spec=Position, current_amount=Decimal("0.5"))
    bot.positions = {"BTCUSDT": pos}
    
    bot.strategy_configs = {"BTCUSDT": MagicMock(mode="live")}
    bot.notifier = AsyncMock()
    bot.state_manager = MagicMock()
    
    await bot._reconcile_positions_with_exchange()
    
    assert "BTCUSDT" in bot.positions
    assert bot.positions["BTCUSDT"].current_amount == Decimal("0.5")
    # No save_positions called for amount mismatch in the implementation (it only saves if ghost removed)
    # Wait, the implementation only calls save_positions if ghost_positions is non-empty.
    # Let's check my implementation again.

@pytest.mark.asyncio
async def test_reconcile_exchange_error(mocker):
    bot = TradingBot.__new__(TradingBot)
    bot.live_exchange = MagicMock()
    bot.live_exchange.get_position_amount = AsyncMock(side_effect=Exception("API Error"))
    
    pos = MagicMock(spec=Position)
    pos.current_amount = Decimal("1.0")
    bot.positions = {"BTCUSDT": pos}
    
    bot.strategy_configs = {"BTCUSDT": MagicMock(mode="live")}
    bot.notifier = AsyncMock()
    
    await bot._reconcile_positions_with_exchange()
    
    # Should keep position on error
    assert "BTCUSDT" in bot.positions
    bot.notifier.send.assert_not_called()
