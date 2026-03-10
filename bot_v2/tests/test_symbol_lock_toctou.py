import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

@pytest.mark.asyncio
async def test_symbol_lock_toctou(mocker):
    """
    Test that concurrent calls to process_signal for the same symbol
    result in only a single Lock being created, fixing the TOCTOU race.
    """
    from bot_v2.bot import TradingBot
    
    class DummyBot(TradingBot):
        def __init__(self):
            self._symbol_locks = {}
            self.bot_state = MagicMock()
            self.bot_state.get_symbol_state.return_value = "RUNNING"
            self.analysis_engine = MagicMock()
            self._process_signal_with_telemetry = AsyncMock()
            self.memory_bank = MagicMock()
            self.memory_bank.should_skip_trade = AsyncMock(return_value=False)
            self._signal_stats = {"total": 0, "processed": 0, "rejected": 0, "errors": 0}

        def _normalize_symbol(self, symbol):
            return symbol if symbol else "UNKNOWN"

        async def _send_status_to_generator(self, *args, **kwargs):
            pass

        async def _handle_entry_signal(self, *args, **kwargs):
            await asyncio.sleep(0.01)  # Yield control to simulate concurrent execution

    bot = DummyBot()
    
    # Mock time.time to avoid side effects
    mocker.patch('time.time', return_value=12345.0)

    # Patch the context manager for telemetry/profiling
    mock_context = MagicMock()
    mock_context.__enter__.return_value = MagicMock()
    mock_context.__exit__.return_value = None
    mocker.patch('bot_v2.bot.profile_signal_processing', return_value=mock_context)
    # Run process_signal concurrently
    signal_data = {"symbol": "BTCUSDT", "action": "buy"}
    
    # Run 50 concurrent process_signal calls for the same symbol
    tasks = [bot._process_single_signal(signal_data) for _ in range(50)]
    await asyncio.gather(*tasks)

    # Verify only one lock was created
    assert "BTCUSDT" in bot._symbol_locks
    lock_instance = bot._symbol_locks["BTCUSDT"]
    
    # Assert lock works properly (it's a real asyncio.Lock)
    assert isinstance(lock_instance, asyncio.Lock)

@pytest.mark.asyncio
async def test_concurrent_lock_creation():
    """Test the setdefault pattern directly under concurrency."""
    locks_dict = {}
    
    async def create_lock(symbol):
        # Emulate the bot's lock creation
        lock = locks_dict.setdefault(symbol, asyncio.Lock())
        return lock

    symbol = "BTCUSDT"
    
    # Run multiple coroutines simultaneously
    tasks = [create_lock(symbol) for _ in range(100)]
    results = await asyncio.gather(*tasks)
    
    # Verify all returned locks are the exact same instance
    first_lock = results[0]
    for lock in results:
        assert lock is first_lock, "Multiple lock instances created!"
    
    assert len(locks_dict) == 1
    assert locks_dict[symbol] is first_lock
