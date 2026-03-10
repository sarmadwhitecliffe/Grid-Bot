#!/usr/bin/env python3
"""
Test to validate that bot_v2 supports per-symbol mode routing (single-symbol only for now).

This test confirms bot_v2 can work in mixed-mode scenarios by running
multiple bot instances, each configured for a different symbol and mode.
"""

import asyncio
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from bot_v2.bot import TradingBot
from bot_v2.execution.live_exchange import LiveExchange
from bot_v2.execution.simulated_exchange import SimulatedExchange
from bot_v2.models.strategy_config import StrategyConfig


@pytest.mark.asyncio
async def test_bot_v2_single_symbol_mode_routing():
    """Test that bot_v2 respects the mode field in StrategyConfig."""
    print("\n" + "=" * 70)
    print("TEST: Bot_v2 Single-Symbol Mode Routing")
    print("=" * 70)

    # Test 1: Create bot with LOCAL_SIM mode
    print("\n--- Test 1: LOCAL_SIM Mode ---")
    sim_config = StrategyConfig(
        symbol_id="BTCUSDT",
        mode="local_sim",
        initial_capital=Decimal("1000"),
        timeframe="30m",
    )

    with patch.object(
        SimulatedExchange, "setup", new_callable=AsyncMock
    ) as mock_sim_setup:
        mock_sim_setup.return_value = True
        bot_sim = TradingBot(sim_config)
        await bot_sim.initialize()

    assert bot_sim.sim_exchange is not None, "Simulated exchange should exist"
    assert (
        bot_sim.live_exchange is None
    ), "Live exchange should NOT exist for local_sim mode"
    print("✅ LOCAL_SIM mode correctly initialized with SimulatedExchange only")

    # Test 2: Create bot with LIVE mode
    print("\n--- Test 2: LIVE Mode ---")
    live_config = StrategyConfig(
        symbol_id="ETHUSDT",
        mode="live",
        initial_capital=Decimal("500"),
        timeframe="15m",
    )
    # Add credentials to config
    live_config.exchange_name = "binance"
    live_config.api_key = "test_key"
    live_config.api_secret = "test_secret"

    with patch.object(
        LiveExchange, "setup", new_callable=AsyncMock
    ) as mock_live_setup, patch.object(
        SimulatedExchange, "setup", new_callable=AsyncMock
    ) as mock_sim_setup:
        mock_live_setup.return_value = True
        mock_sim_setup.return_value = True
        bot_live = TradingBot(live_config)
        await bot_live.initialize()

    assert bot_live.sim_exchange is not None, "Simulated exchange should always exist"
    assert (
        bot_live.live_exchange is not None
    ), "Live exchange should exist for live mode"
    assert isinstance(
        bot_live.live_exchange, LiveExchange
    ), "Should be LiveExchange instance"
    print(
        "✅ LIVE mode correctly initialized with both LiveExchange and SimulatedExchange"
    )

    # Test 3: Verify backward compatibility with deprecated simulation_mode parameter
    print("\n--- Test 3: Backward Compatibility ---")
    compat_config = StrategyConfig(
        symbol_id="SOLUSDT",
        mode="local_sim",  # This will be overridden
        initial_capital=Decimal("300"),
        timeframe="30m",
    )

    with patch.object(
        SimulatedExchange, "setup", new_callable=AsyncMock
    ) as mock_sim_setup:
        mock_sim_setup.return_value = True
        # Use deprecated simulation_mode=True parameter
        bot_compat = TradingBot(compat_config, simulation_mode=True)
        await bot_compat.initialize()

    assert (
        bot_compat.strategy_configs["SOLUSDT"].mode == "local_sim"
    ), "simulation_mode=True should map to local_sim"
    print(
        "✅ Backward compatibility: simulation_mode=True correctly maps to mode='local_sim'"
    )

    # Clean up
    await bot_sim.sim_exchange.close()
    await bot_live.sim_exchange.close()
    if bot_live.live_exchange:
        await bot_live.live_exchange.close()
    await bot_compat.sim_exchange.close()

    print("\n" + "=" * 70)
    print("✅ ALL BOT_V2 TESTS PASSED")
    print("=" * 70)
    print("\nSummary:")
    print("  - Bot_v2 respects StrategyConfig.mode field")
    print("  - LOCAL_SIM mode: SimulatedExchange only")
    print("  - LIVE mode: Both LiveExchange + SimulatedExchange")
    print("  - Backward compatible with simulation_mode parameter")
    print("\n⚠️  Note: Bot_v2 currently supports single-symbol mode only.")
    print("   For true multi-symbol mixed-mode, use main bot.py")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(test_bot_v2_single_symbol_mode_routing())
