import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot_v2.bot import TradingBot as Bot


@pytest.mark.asyncio
async def test_signal_concurrency_limit():
    """
    Verify that the bot limits concurrent signal processing to MAX_SIGNAL_CONCURRENCY.
    """
    # Setup
    with patch.dict("os.environ", {"MAX_SIGNAL_CONCURRENCY": "2"}):
        # Pass a dict config so keys are strings
        config = {"BTC/USDT": MagicMock(), "ETH/USDT": MagicMock()}
        # Add dynamic keys for the loop
        for i in range(5):
            config[f"BTC/USDT:{i}"] = MagicMock()

        bot = Bot(config=config)
        # Mock dependencies to avoid real processing
        bot.exchange = AsyncMock()
        bot.risk_manager = MagicMock()
        bot.capital_manager = MagicMock()
        bot.position_manager = MagicMock()
        bot.exit_engine = MagicMock()

        # Mock _process_single_signal to simulate work and track concurrency
        active_tasks = 0
        max_seen_tasks = 0

        async def mock_process(signal):
            nonlocal active_tasks, max_seen_tasks
            active_tasks += 1
            max_seen_tasks = max(max_seen_tasks, active_tasks)
            await asyncio.sleep(0.1)  # Simulate work
            active_tasks -= 1

        bot._process_single_signal = mock_process

        # Create 5 signals (dicts) and put in queue
        for i in range(5):
            bot.signal_queue.put_nowait(
                {"symbol": f"BTC/USDT:{i}", "action": "buy", "source": "dts"}
            )

        # Run processing
        await bot._process_signals()

        # Assertions
        assert (
            max_seen_tasks <= 2
        ), f"Concurrency exceeded limit! Max seen: {max_seen_tasks}"
        assert max_seen_tasks > 0, "No tasks were processed"


@pytest.mark.asyncio
async def test_symbol_locking():
    """
    Verify that signals for the same symbol are processed sequentially.
    """
    config = {"ETH/USDT": MagicMock()}
    bot = Bot(config=config)
    bot.exchange = AsyncMock()
    bot.risk_manager = MagicMock()
    bot.capital_manager = MagicMock()
    bot.position_manager = MagicMock()
    bot.exit_engine = MagicMock()

    # Track start/end times
    execution_log = []

    async def mock_handle_entry(symbol, side, metadata, tracker):
        start_time = asyncio.get_running_loop().time()
        execution_log.append(("start", symbol, start_time))
        await asyncio.sleep(0.05)
        end_time = asyncio.get_running_loop().time()
        execution_log.append(("end", symbol, end_time))

    bot._handle_entry_signal = mock_handle_entry

    # Disable deduplication for this test
    bot._dedup_window = 0

    # Two signals for same symbol
    bot.signal_queue.put_nowait(
        {"symbol": "ETH/USDT", "action": "buy", "source": "dts"}
    )
    bot.signal_queue.put_nowait(
        {"symbol": "ETH/USDT", "action": "buy", "source": "dts"}
    )

    await bot._process_signals()

    # Analyze log
    starts = [e[2] for e in execution_log if e[0] == "start"]

    assert len(starts) == 2

    # Check for overlap
    # Since we sleep 0.05, if they are sequential, the difference between starts should be >= 0.05
    assert (
        abs(starts[1] - starts[0]) >= 0.045
    ), "Signals for same symbol ran in parallel"
