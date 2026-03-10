from unittest.mock import AsyncMock

import pytest

from bot_v2.bot import TradingBot
from bot_v2.models.strategy_config import StrategyConfig


@pytest.mark.asyncio
async def test_tradingbot_shutdown_closes_exchanges(tmp_path):
    # Create a minimal single-symbol config
    # Use from_dict factory to avoid relying on constructor signature
    # Create as a local_sim so TradingBot does not try to initialize a real live exchange
    cfg = StrategyConfig.from_dict(
        "TEST/USDT", {"mode": "local_sim", "data_dir": str(tmp_path)}
    )

    bot = TradingBot(cfg)

    # Inject mocked exchanges to verify shutdown attempts to close them
    bot.live_exchange = AsyncMock()
    bot.sim_exchange = AsyncMock()

    # Ensure they have close coroutines
    bot.live_exchange.close = AsyncMock()
    bot.sim_exchange.close = AsyncMock()

    # Run shutdown
    await bot.shutdown()

    bot.live_exchange.close.assert_awaited_once()
    bot.sim_exchange.close.assert_awaited_once()
