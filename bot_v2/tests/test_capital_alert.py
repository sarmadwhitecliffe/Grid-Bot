import asyncio
import pytest
from decimal import Decimal
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, AsyncMock

from bot_v2.risk.capital_manager import CapitalManager

@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)

@pytest.mark.asyncio
async def test_capital_depletion_alert(temp_dir):
    manager = CapitalManager(temp_dir)
    
    # Set initial capital
    await manager.update_capital("BTCUSDT", Decimal("5.00") - Decimal("1000.00")) 
    # ^ Initial is 1000, so we subtract 995 to get to 5.
    
    assert await manager.get_capital("BTCUSDT") == Decimal("5.00")
    
    # Set up alert callback
    alert_mock = AsyncMock()
    manager.set_critical_alert_callback(alert_mock)
    
    # Apply loss that depletes capital
    new_cap = await manager.update_capital("BTCUSDT", Decimal("-10.00"))
    
    assert new_cap == Decimal("0")
    assert await manager.get_capital("BTCUSDT") == Decimal("0")
    
    # Verify alert was triggered
    # asyncio.ensure_future is used, so we might need a small sleep or wait
    await asyncio.sleep(0.1)
    alert_mock.assert_called_once()
    args, kwargs = alert_mock.call_args
    assert args[0] == "BTCUSDT"
    assert "CAPITAL DEPLETED" in args[1]

@pytest.mark.asyncio
async def test_is_halted(temp_dir):
    manager = CapitalManager(temp_dir)
    
    # Initial capital is $1000
    assert not await manager.is_halted("BTCUSDT")
    
    # Deplete capital
    await manager.update_capital("BTCUSDT", Decimal("-1000.00"))
    assert await manager.is_halted("BTCUSDT")
    
    # Verify positive capital is not halted
    await manager.update_capital("BTCUSDT", Decimal("1.00"))
    assert not await manager.is_halted("BTCUSDT")

@pytest.mark.asyncio
async def test_bot_halt_on_zero_capital(mocker):
    from bot_v2.bot import TradingBot
    from bot_v2.models.position import PositionSide
    
    # Create dummy bot with mocked dependencies
    class DummyBot(TradingBot):
        def __init__(self):
            self.capital_manager = MagicMock()
            self.risk_manager = MagicMock()
            self.strategy_configs = {"BTCUSDT": MagicMock()}
            self._symbol_locks = {}
            self.notifier = AsyncMock()
            self.bot_state = MagicMock()
            self.bot_state.get_symbol_state.return_value = "RUNNING"
            self.cost_filter = MagicMock()
            self.cost_filter.is_cost_floor_met.return_value = True
            self.positions = {}
            self._signal_stats = {"total": 0, "processed": 0, "rejected": 0, "errors": 0}

        def _get_config(self, symbol):
            cfg = MagicMock()
            cfg.timeframe = "1m"
            cfg.atr_period = 14
            return cfg

        def _get_exchange(self, symbol):
            mock_ex = MagicMock()
            mock_ex.get_market_price = AsyncMock(return_value=Decimal("50000"))
            import pandas as pd
            df = pd.DataFrame({
                "high": [50100] * 20,
                "low": [49900] * 20,
                "close": [50000] * 20
            })
            mock_ex.fetch_ohlcv = AsyncMock(return_value=df)
            return mock_ex

        def _normalize_symbol(self, symbol): return symbol
        async def _send_status_to_generator(self, *args, **kwargs): pass

    bot = DummyBot()
    
    # Mock capital to be 0
    bot.capital_manager.get_capital = AsyncMock(return_value=Decimal("0"))
    
    # Mock other things to avoid errors
    mocker.patch('time.time', return_value=12345.0)
    mock_context = MagicMock()
    mocker.patch('bot_v2.bot.profile_signal_processing', return_value=mock_context)

    # Call _process_single_signal
    signal = {"symbol": "BTCUSDT", "action": "buy"}
    await bot._process_single_signal(signal)
    
    # Verify risk_manager was NOT called because it was rejected early
    bot.risk_manager.calculate_position_params.assert_not_called()
    
    # Verify capital_manager was called
    bot.capital_manager.get_capital.assert_called_with("BTCUSDT")
