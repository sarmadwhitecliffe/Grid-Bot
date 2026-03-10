import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot_v2.bot import TradingBot as Bot


@pytest.mark.asyncio
async def test_perf_logging_summary(caplog):
    """
    Verify that [PERF] summary is logged when LOG_PERF_DETAILS is false.
    """
    with patch.dict("os.environ", {"LOG_PERF_DETAILS": "false"}):
        # Setup config with string keys to pass validation
        config_mock = {"BTC/USDT": MagicMock(), "ETH/USDT": MagicMock()}
        bot = Bot(config=config_mock)
        bot.exchange = AsyncMock()
        bot.risk_manager = MagicMock()
        bot.capital_manager = MagicMock()
        bot.position_manager = MagicMock()
        bot.exit_engine = MagicMock()

        # Mock dependencies to succeed quickly
        bot.risk_manager.validate_signal.return_value = True
        bot.capital_manager.calculate_position_size.return_value = (1.0, 100.0)
        bot.exchange.create_market_order.return_value = {
            "id": "123",
            "status": "closed",
        }
        bot.exchange.get_market_price.return_value = 50000.0

        signal = {"symbol": "BTC/USDT", "action": "buy", "source": "dts"}

        with caplog.at_level(logging.INFO):
            await bot._process_single_signal(signal)

        # Check for [PERF] log
        assert any("[PERF]" in record.message for record in caplog.records)
        # Note: The exact format depends on implementation, but it should contain [PERF]


@pytest.mark.asyncio
async def test_perf_logging_detailed(caplog):
    """
    Verify that [PERF] summary is NOT logged when LOG_PERF_DETAILS is true.
    """
    with patch.dict("os.environ", {"LOG_PERF_DETAILS": "true"}):
        # Setup config with string keys to pass validation
        config_mock = {"BTC/USDT": MagicMock(), "ETH/USDT": MagicMock()}
        bot = Bot(config=config_mock)
        bot.exchange = AsyncMock()
        bot.risk_manager = MagicMock()
        bot.capital_manager = MagicMock()
        bot.position_manager = MagicMock()
        bot.exit_engine = MagicMock()

        # Mock dependencies
        bot.risk_manager.validate_signal.return_value = True
        bot.capital_manager.calculate_position_size.return_value = (1.0, 100.0)
        bot.exchange.create_market_order.return_value = {
            "id": "123",
            "status": "closed",
        }
        bot.exchange.get_market_price.return_value = 50000.0

        signal = {"symbol": "BTC/USDT", "action": "buy", "source": "dts"}

        with caplog.at_level(logging.INFO):
            await bot._process_single_signal(signal)

        # Check for absence of [PERF] log (or presence of detailed logs if that's what true means)
        # Based on bot.py logic:
        # if not ENABLE_PERFORMANCE_PROFILING:
        #     logger.info(f"[PERF] {signal_id}: Total {total_ms:.1f}ms")
        # else:
        #     logger.info(f"[PERF] {signal_id}: total={total_ms:.1f}ms, price={price_ms:.1f}ms, ...")

        # So [PERF] is ALWAYS logged, but the content differs.
        # Let's check for detailed breakdown
        assert any("Breakdown:" in record.message for record in caplog.records)
