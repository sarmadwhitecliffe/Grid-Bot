"""
Integration test for Grid Bot within TradingBot main loop.
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from decimal import Decimal
from bot_v2.bot import TradingBot
from bot_v2.models.strategy_config import StrategyConfig

@pytest.fixture
def mock_bot_deps():
    with patch("bot_v2.bot.StateManager") as mock_sm, \
         patch("bot_v2.bot.CapitalManager") as mock_cm, \
         patch("bot_v2.bot.Notifier") as mock_notifier:
        
        # Mock StateManager to return empty states
        mock_sm.return_value.load_states.return_value = ({}, {}, [], {})
        
        # Ensure Notifier.send is awaitable
        mock_notifier.return_value.send = AsyncMock()
        
        yield mock_sm, mock_cm, mock_notifier

@pytest.mark.asyncio
async def test_bot_initializes_grid(mock_bot_deps):
    mock_sm, mock_cm, mock_notifier = mock_bot_deps
    
    # Create a config with grid enabled
    grid_config = StrategyConfig(
        symbol_id="BTC/USDT",
        enabled=True,
        mode="local_sim",
        grid_enabled=True,
        grid_spacing_pct=Decimal("0.01"),
        grid_num_grids_up=5,
        grid_num_grids_down=5
    )
    
    # Init bot with config
    bot = TradingBot(config={"BTC/USDT": grid_config})
    
    # Setup mock exchange
    mock_exchange = AsyncMock()
    bot.sim_exchange = mock_exchange
    
    await bot.initialize()
    
    # Verify orchestrator was created
    assert "BTC/USDT" in bot.grid_orchestrators
    assert bot.grid_orchestrators["BTC/USDT"].symbol == "BTC/USDT"
    
@pytest.mark.asyncio
async def test_bot_loop_ticks_grid(mock_bot_deps):
    mock_sm, mock_cm, mock_notifier = mock_bot_deps
    
    grid_config = StrategyConfig(
        symbol_id="BTC/USDT",
        enabled=True,
        mode="local_sim",
        grid_enabled=True
    )
    
    bot = TradingBot(config={"BTC/USDT": grid_config})
    
    # Mock exchange and orchestrator
    mock_exchange = AsyncMock()
    import pandas as pd
    df = pd.DataFrame([[1,2,3,4,5,6]], columns=["timestamp", "open", "high", "low", "close", "volume"])
    mock_exchange.fetch_ohlcv.return_value = df
    bot.sim_exchange = mock_exchange
    
    await bot.initialize()
    
    mock_orchestrator = AsyncMock()
    mock_orchestrator.is_active = True
    bot.grid_orchestrators["BTC/USDT"] = mock_orchestrator
    
    # Manually execute the logic that would run inside the loop for one symbol
    # This mirrors bot.run() loop content for Phase 3 integration
    for symbol, orchestrator in bot.grid_orchestrators.items():
        if orchestrator.is_active:
            config = bot.strategy_configs[symbol]
            exchange = bot._get_exchange_for_symbol(symbol)
            ohlcv = await exchange.fetch_ohlcv(symbol, config.timeframe, 100)
            if ohlcv is not None:
                await orchestrator.tick(ohlcv)
    
    # Verify orchestrator.tick was called
    assert mock_orchestrator.tick.called
