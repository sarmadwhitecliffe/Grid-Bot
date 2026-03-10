from datetime import datetime, timezone

import pytest

from bot_v2.bot import TradingBot
from bot_v2.models.strategy_config import StrategyConfig


@pytest.mark.asyncio
async def test_summary_includes_net_pnl_percentage(tmp_path):
    # Prepare a minimal strategy config and set data_dir to a temporary path
    cfg = StrategyConfig(symbol_id="TEST")
    cfg.data_dir = tmp_path

    # Instantiate bot with the temporary data directory
    bot = TradingBot(cfg, simulation_mode=True)

    # Ensure state manager uses the same data dir
    # Prepare a single trade in the recent timeframe with pnl = 50 USD
    now = datetime.now(timezone.utc)
    trade = {
        "timestamp": now.isoformat(),
        "pnl_usd": "50.0",
        "realized_r_multiple": "0.5",
        "exit_reason": "TP1",
        "time_to_exit_sec": 3600,
    }

    # Save history via state manager
    bot.state_manager.save_history([trade])

    # Update bot's in-memory trade history as well, since get_summary_message uses it
    bot.trade_history = [trade]

    # Set a canonical capital for the portfolio (1000 USD)
    # CapitalManager stores capitals as strings in its in-memory _capitals
    bot.capital_manager._capitals = {
        "TESTSYM": {
            "capital": "1000.00",
            "tier": "PROBATION",
            "last_notified_tier": "PROBATION",
        }
    }

    # Generate summary
    summary = await bot.get_summary_message(hours=24)

    # Net PnL is +50 USD on a 1000 USD capital -> +5.00%
    assert "+5.00%" in summary
    assert "+50.00" in summary or "+50.0" in summary
