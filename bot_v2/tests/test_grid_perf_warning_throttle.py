import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot_v2.bot as bot_module
from bot_v2.bot import TradingBot


@pytest.mark.asyncio
async def test_grid_perf_warning_is_rate_limited(caplog, tmp_path):
    """Repeated high-latency ticks should not emit warning on every tick."""
    symbol_cfg = MagicMock()
    symbol_cfg.timeframe = "1h"
    symbol_cfg.data_dir = str(tmp_path)
    symbol_cfg.mode = "local_sim"
    symbol_cfg.initial_capital = 1000.0

    config_mock = {
        "BTC/USDT": symbol_cfg
    }
    bot = TradingBot(config=config_mock)

    # One active orchestrator, but keep tick processing lightweight.
    bot.grid_orchestrators = {"BTC/USDT": MagicMock(is_active=True)}
    exchange_mock = MagicMock()
    exchange_mock.fetch_ohlcv = AsyncMock(return_value=object())
    bot._get_exchange_for_symbol = MagicMock(return_value=exchange_mock)
    bot._process_grid_orchestrator_tick = AsyncMock(return_value=None)

    with patch.object(bot_module, "ENABLE_GRID_LATENCY_LOGGING", True), patch.object(
        bot_module, "GRID_LATENCY_WARN_MS", 1500.0
    ), patch.object(bot_module, "GRID_LATENCY_WARN_INTERVAL_SECS", 60.0), patch.object(
        bot_module, "GRID_LATENCY_WARN_DELTA_MS", 500.0
    ), patch.object(
        bot_module.time,
        "perf_counter",
        side_effect=[0.0, 2.2, 2.25, 10.0, 12.2, 12.25],
    ), patch.object(
        bot_module.time,
        "time",
        return_value=1000.0,
    ):
        with caplog.at_level(logging.WARNING):
            await bot._run_grid_orchestrators_tick()
            await bot._run_grid_orchestrators_tick()

    perf_warnings = [r for r in caplog.records if "[GRID][PERF]" in r.message]
    assert len(perf_warnings) == 1
    assert bot._grid_latency_suppressed_count == 1
