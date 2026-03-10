"""
tests/test_price_feed.py
-------------------------
Unit tests for src/data/price_feed.py.

Tests verify:
  - Historical OHLCV fetching with Parquet cache logic
  - Cache freshness validation based on file modification time
  - Symbol name sanitization for safe filenames
  - Real-time price polling with callback registration
  - Graceful start/stop of async polling loops
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from config.settings import GridBotSettings
from src.data.price_feed import COLUMNS, TIMEFRAME_SECONDS, PriceFeed
from src.exchange.exchange_client import ExchangeClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_exchange_client() -> MagicMock:
    """Return a mocked ExchangeClient with async methods."""
    client = MagicMock(spec=ExchangeClient)
    
    # Mock fetch_ohlcv to return raw OHLCV data
    raw_ohlcv = [
        [1704067200000, 30000.0, 30100.0, 29900.0, 30050.0, 100.0],  # 2024-01-01 00:00
        [1704070800000, 30050.0, 30150.0, 29950.0, 30080.0, 110.0],  # 2024-01-01 01:00
        [1704074400000, 30080.0, 30200.0, 30000.0, 30120.0, 120.0],  # 2024-01-01 02:00
    ]
    client.fetch_ohlcv = AsyncMock(return_value=raw_ohlcv)
    
    # Mock get_ticker for real-time price
    client.get_ticker = AsyncMock(return_value={"last": 30000.0})
    
    return client


@pytest.fixture
def test_settings(tmp_path: Path) -> GridBotSettings:
    """Return test settings with temporary cache directory."""
    from config.settings import load_yaml_config
    
    yaml_defaults = load_yaml_config()
    overrides = {
        "EXCHANGE_ID": "binance",
        "MARKET_TYPE": "spot",
        "API_KEY": "test_key",
        "API_SECRET": "test_secret",
        "SYMBOL": "BTC/USDT",
        "OHLCV_TIMEFRAME": "1h",
        "OHLCV_LIMIT": 200,
        "POLL_INTERVAL_SEC": 1,
        "OHLCV_CACHE_DIR": tmp_path / "ohlcv_cache",
        "STATE_FILE": tmp_path / "test_state.json",
        "LOG_FILE": tmp_path / "test.log",
    }
    return GridBotSettings(**{**yaml_defaults, **overrides})


@pytest.fixture
def price_feed(
    mock_exchange_client: MagicMock,
    test_settings: GridBotSettings,
) -> PriceFeed:
    """Return a PriceFeed instance with mocked dependencies."""
    return PriceFeed(client=mock_exchange_client, settings=test_settings)


@pytest.fixture
def sample_cached_df() -> pd.DataFrame:
    """Return a sample OHLCV DataFrame for cache testing."""
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=3, freq="1h"),
            "open": [30000.0, 30050.0, 30080.0],
            "high": [30100.0, 30150.0, 30200.0],
            "low": [29900.0, 29950.0, 30000.0],
            "close": [30050.0, 30080.0, 30120.0],
            "volume": [100.0, 110.0, 120.0],
        }
    )


# ---------------------------------------------------------------------------
# Initialization Tests
# ---------------------------------------------------------------------------


class TestPriceFeedInit:
    """Test PriceFeed initialization."""
    
    def test_init_creates_cache_directory(
        self,
        mock_exchange_client: MagicMock,
        test_settings: GridBotSettings,
    ) -> None:
        """Cache directory should be created if it doesn't exist."""
        feed = PriceFeed(client=mock_exchange_client, settings=test_settings)
        assert feed.cache_dir.exists()
        assert feed.cache_dir.is_dir()
    
    def test_init_sets_attributes(
        self,
        mock_exchange_client: MagicMock,
        test_settings: GridBotSettings,
    ) -> None:
        """Instance attributes should be properly initialized."""
        feed = PriceFeed(client=mock_exchange_client, settings=test_settings)
        assert feed.client is mock_exchange_client
        assert feed.settings is test_settings
        assert feed.cache_dir == test_settings.OHLCV_CACHE_DIR
        assert feed._current_price is None
        assert feed._price_callbacks == []


# ---------------------------------------------------------------------------
# Cache Path Tests
# ---------------------------------------------------------------------------


class TestCachePath:
    """Test cache path generation and symbol sanitization."""
    
    def test_cache_path_sanitizes_symbol(
        self,
        price_feed: PriceFeed,
        test_settings: GridBotSettings,
    ) -> None:
        """Symbol '/' should be replaced with '_' for filesystem safety."""
        cache_path = price_feed._cache_path()
        expected_filename = "BTC_USDT_1h.parquet"
        assert cache_path.name == expected_filename
    
    def test_cache_path_includes_timeframe(
        self,
        price_feed: PriceFeed,
    ) -> None:
        """Cache filename should include the timeframe."""
        cache_path = price_feed._cache_path()
        assert "1h" in cache_path.name
    
    def test_cache_path_is_in_cache_directory(
        self,
        price_feed: PriceFeed,
        test_settings: GridBotSettings,
    ) -> None:
        """Cache file should be placed in the configured cache directory."""
        cache_path = price_feed._cache_path()
        assert cache_path.parent == test_settings.OHLCV_CACHE_DIR
    
    def test_cache_path_has_parquet_extension(
        self,
        price_feed: PriceFeed,
    ) -> None:
        """Cache file should have .parquet extension."""
        cache_path = price_feed._cache_path()
        assert cache_path.suffix == ".parquet"


# ---------------------------------------------------------------------------
# Cache Freshness Tests
# ---------------------------------------------------------------------------


class TestCacheFreshness:
    """Test cache freshness validation logic."""
    
    def test_missing_cache_is_not_fresh(
        self,
        price_feed: PriceFeed,
    ) -> None:
        """Non-existent cache file should not be considered fresh."""
        non_existent = price_feed.cache_dir / "does_not_exist.parquet"
        assert price_feed._cache_is_fresh(non_existent) is False
    
    def test_fresh_cache_within_candle_period(
        self,
        price_feed: PriceFeed,
        sample_cached_df: pd.DataFrame,
    ) -> None:
        """Cache modified within candle period should be fresh."""
        cache_path = price_feed._cache_path()
        sample_cached_df.to_parquet(cache_path, index=False)
        
        # File was just written, so it's fresh
        assert price_feed._cache_is_fresh(cache_path) is True
    
    @patch("src.data.price_feed.datetime")
    def test_stale_cache_outside_candle_period(
        self,
        mock_datetime: MagicMock,
        price_feed: PriceFeed,
        sample_cached_df: pd.DataFrame,
    ) -> None:
        """Cache modified more than one candle period ago should be stale."""
        cache_path = price_feed._cache_path()
        sample_cached_df.to_parquet(cache_path, index=False)
        
        # Simulate that 2 hours have passed (cache is for 1h timeframe)
        real_mtime = cache_path.stat().st_mtime
        fake_now = datetime.fromtimestamp(real_mtime, tz=timezone.utc) + timedelta(hours=2)
        
        mock_datetime.now.return_value = fake_now
        mock_datetime.return_value.timestamp.return_value = fake_now.timestamp()
        
        assert price_feed._cache_is_fresh(cache_path) is False
    
    def test_cache_freshness_uses_timeframe_seconds(
        self,
        price_feed: PriceFeed,
    ) -> None:
        """Freshness threshold should match the candle period."""
        # Verify TIMEFRAME_SECONDS is used correctly
        assert TIMEFRAME_SECONDS["1h"] == 3600
        assert TIMEFRAME_SECONDS["1m"] == 60
        assert TIMEFRAME_SECONDS["1d"] == 86400


# ---------------------------------------------------------------------------
# OHLCV DataFrame Tests
# ---------------------------------------------------------------------------


class TestGetOHLCVDataframe:
    """Test historical OHLCV fetching with cache logic."""
    
    @pytest.mark.asyncio
    async def test_cache_miss_triggers_api_fetch(
        self,
        price_feed: PriceFeed,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Missing cache should trigger fresh API fetch."""
        df = await price_feed.get_ohlcv_dataframe()
        
        # Verify API was called
        mock_exchange_client.fetch_ohlcv.assert_called_once_with(
            timeframe="1h",
            limit=200,
        )
        
        # Verify DataFrame structure
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == COLUMNS
    
    @pytest.mark.asyncio
    async def test_fresh_cache_loaded_without_api_call(
        self,
        price_feed: PriceFeed,
        mock_exchange_client: MagicMock,
        sample_cached_df: pd.DataFrame,
    ) -> None:
        """Fresh cache should be loaded without calling API."""
        # Write fresh cache
        cache_path = price_feed._cache_path()
        sample_cached_df.to_parquet(cache_path, index=False)
        
        df = await price_feed.get_ohlcv_dataframe()
        
        # Verify API was NOT called
        mock_exchange_client.fetch_ohlcv.assert_not_called()
        
        # Verify cached data was loaded
        pd.testing.assert_frame_equal(df, sample_cached_df)
    
    @pytest.mark.asyncio
    async def test_stale_cache_triggers_refetch(
        self,
        price_feed: PriceFeed,
        mock_exchange_client: MagicMock,
        sample_cached_df: pd.DataFrame,
    ) -> None:
        """Stale cache should trigger fresh API fetch."""
        # Write cache and make it stale by patching freshness check
        cache_path = price_feed._cache_path()
        sample_cached_df.to_parquet(cache_path, index=False)
        
        with patch.object(price_feed, "_cache_is_fresh", return_value=False):
            df = await price_feed.get_ohlcv_dataframe()
        
        # Verify API was called despite cache existing
        mock_exchange_client.fetch_ohlcv.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_api_fetch_writes_to_cache(
        self,
        price_feed: PriceFeed,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Fresh API fetch should write data to cache."""
        cache_path = price_feed._cache_path()
        
        # Ensure cache doesn't exist
        if cache_path.exists():
            cache_path.unlink()
        
        df = await price_feed.get_ohlcv_dataframe()
        
        # Verify cache file was created
        assert cache_path.exists()
        
        # Verify cached data matches returned data
        cached_df = pd.read_parquet(cache_path)
        pd.testing.assert_frame_equal(df, cached_df)
    
    @pytest.mark.asyncio
    async def test_dataframe_has_correct_columns(
        self,
        price_feed: PriceFeed,
    ) -> None:
        """Returned DataFrame should have correct columns in order."""
        df = await price_feed.get_ohlcv_dataframe()
        assert list(df.columns) == COLUMNS
    
    @pytest.mark.asyncio
    async def test_dataframe_timestamps_are_utc(
        self,
        price_feed: PriceFeed,
    ) -> None:
        """Timestamp column should be datetime with UTC timezone."""
        df = await price_feed.get_ohlcv_dataframe()
        assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])
        assert df["timestamp"].dt.tz == timezone.utc
    
    @pytest.mark.asyncio
    async def test_dataframe_is_sorted_by_timestamp(
        self,
        price_feed: PriceFeed,
    ) -> None:
        """DataFrame should be sorted ascending by timestamp."""
        df = await price_feed.get_ohlcv_dataframe()
        assert df["timestamp"].is_monotonic_increasing
    
    @pytest.mark.asyncio
    async def test_dataframe_index_is_reset(
        self,
        price_feed: PriceFeed,
    ) -> None:
        """DataFrame index should be reset to default integer range."""
        df = await price_feed.get_ohlcv_dataframe()
        assert df.index.tolist() == list(range(len(df)))


# ---------------------------------------------------------------------------
# Real-Time Price Callback Tests
# ---------------------------------------------------------------------------


class TestPriceCallbacks:
    """Test real-time price callback registration and invocation."""
    
    def test_register_price_callback(
        self,
        price_feed: PriceFeed,
    ) -> None:
        """Callbacks should be registered successfully."""
        async def dummy_callback(price: float) -> None:
            pass
        
        price_feed.register_price_callback(dummy_callback)
        assert dummy_callback in price_feed._price_callbacks
    
    def test_register_multiple_callbacks(
        self,
        price_feed: PriceFeed,
    ) -> None:
        """Multiple callbacks should be registered in order."""
        async def callback1(price: float) -> None:
            pass
        
        async def callback2(price: float) -> None:
            pass
        
        price_feed.register_price_callback(callback1)
        price_feed.register_price_callback(callback2)
        
        assert len(price_feed._price_callbacks) == 2
        assert price_feed._price_callbacks[0] is callback1
        assert price_feed._price_callbacks[1] is callback2
    
    def test_current_price_initial_value(
        self,
        price_feed: PriceFeed,
    ) -> None:
        """Current price should be None before first fetch."""
        assert price_feed.current_price is None
    
    @pytest.mark.asyncio
    async def test_callbacks_invoked_on_price_change(
        self,
        price_feed: PriceFeed,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Callbacks should be invoked when price changes."""
        prices_received: List[float] = []
        
        async def track_price(price: float) -> None:
            prices_received.append(price)
        
        price_feed.register_price_callback(track_price)
        
        # Mock ticker to return changing prices
        mock_exchange_client.get_ticker.side_effect = [
            {"last": 30000.0},
            {"last": 30100.0},
            {"last": 30200.0},
        ]
        
        # Start polling and let it run a few cycles
        polling_task = asyncio.create_task(price_feed.start_real_time_polling())
        await asyncio.sleep(2.5)  # Allow 2-3 poll cycles
        polling_task.cancel()
        
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
        
        # Verify callbacks were invoked
        assert len(prices_received) >= 2
        assert 30000.0 in prices_received
        assert 30100.0 in prices_received
    
    @pytest.mark.asyncio
    async def test_callbacks_not_invoked_when_price_unchanged(
        self,
        price_feed: PriceFeed,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Callbacks should NOT be invoked when price doesn't change."""
        invocation_count = 0
        
        async def count_invocations(price: float) -> None:
            nonlocal invocation_count
            invocation_count += 1
        
        price_feed.register_price_callback(count_invocations)
        
        # Mock ticker to always return same price
        mock_exchange_client.get_ticker.return_value = {"last": 30000.0}
        
        # Start polling and let it run
        polling_task = asyncio.create_task(price_feed.start_real_time_polling())
        await asyncio.sleep(2.5)
        polling_task.cancel()
        
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
        
        # Should be invoked only once (first price change from None to 30000.0)
        assert invocation_count == 1
    
    @pytest.mark.asyncio
    async def test_current_price_updated_on_fetch(
        self,
        price_feed: PriceFeed,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Current price property should be updated after fetch."""
        mock_exchange_client.get_ticker.return_value = {"last": 30500.0}
        
        polling_task = asyncio.create_task(price_feed.start_real_time_polling())
        await asyncio.sleep(1.5)  # Allow one poll cycle
        polling_task.cancel()
        
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
        
        assert price_feed.current_price == 30500.0


# ---------------------------------------------------------------------------
# Real-Time Polling Loop Tests
# ---------------------------------------------------------------------------


class TestRealTimePolling:
    """Test the real-time polling loop behavior."""
    
    @pytest.mark.asyncio
    async def test_polling_loop_calls_get_ticker(
        self,
        price_feed: PriceFeed,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Polling loop should call get_ticker periodically."""
        polling_task = asyncio.create_task(price_feed.start_real_time_polling())
        await asyncio.sleep(1.5)  # Allow at least 1 poll
        polling_task.cancel()
        
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
        
        assert mock_exchange_client.get_ticker.call_count >= 1
    
    @pytest.mark.asyncio
    async def test_polling_loop_continues_on_exception(
        self,
        price_feed: PriceFeed,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Polling loop should continue running after exception."""
        # First call raises exception, subsequent calls succeed
        mock_exchange_client.get_ticker.side_effect = [
            Exception("Network error"),
            {"last": 30000.0},
            {"last": 30100.0},
        ]
        
        price_feed_received: List[float] = []
        
        async def track_price(price: float) -> None:
            price_feed_received.append(price)
        
        price_feed.register_price_callback(track_price)
        
        polling_task = asyncio.create_task(price_feed.start_real_time_polling())
        await asyncio.sleep(3.5)  # Allow time for recovery
        polling_task.cancel()
        
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
        
        # Should have recovered and received prices after the error
        assert len(price_feed_received) >= 1
    
    @pytest.mark.asyncio
    async def test_polling_respects_interval(
        self,
        price_feed: PriceFeed,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Polling should respect POLL_INTERVAL_SEC setting."""
        start_time = asyncio.get_event_loop().time()
        
        polling_task = asyncio.create_task(price_feed.start_real_time_polling())
        await asyncio.sleep(2.5)
        polling_task.cancel()
        
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
        
        elapsed = asyncio.get_event_loop().time() - start_time
        call_count = mock_exchange_client.get_ticker.call_count
        
        # With 1s interval, should have ~2-3 calls in 2.5s
        assert call_count >= 2
        assert call_count <= 4
    
    @pytest.mark.asyncio
    async def test_polling_task_cancellation(
        self,
        price_feed: PriceFeed,
    ) -> None:
        """Polling task should be cancellable without hanging."""
        polling_task = asyncio.create_task(price_feed.start_real_time_polling())
        await asyncio.sleep(0.5)
        
        # Cancel should work cleanly
        polling_task.cancel()
        
        with pytest.raises(asyncio.CancelledError):
            await polling_task


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestPriceFeedIntegration:
    """Integration tests combining multiple PriceFeed features."""
    
    @pytest.mark.asyncio
    async def test_end_to_end_cache_and_polling(
        self,
        price_feed: PriceFeed,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Test complete workflow: fetch OHLCV, start polling."""
        # Fetch historical data (creates cache)
        df = await price_feed.get_ohlcv_dataframe()
        assert len(df) == 3
        assert price_feed._cache_path().exists()
        
        # Start real-time polling
        prices: List[float] = []
        
        async def collect_prices(price: float) -> None:
            prices.append(price)
        
        price_feed.register_price_callback(collect_prices)
        
        mock_exchange_client.get_ticker.return_value = {"last": 31000.0}
        
        polling_task = asyncio.create_task(price_feed.start_real_time_polling())
        await asyncio.sleep(1.5)
        polling_task.cancel()
        
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
        
        assert len(prices) >= 1
        assert price_feed.current_price == 31000.0
    
    @pytest.mark.asyncio
    async def test_symbol_with_complex_characters(
        self,
        mock_exchange_client: MagicMock,
        test_settings: GridBotSettings,
    ) -> None:
        """Test cache path sanitization with various symbol formats."""
        # Test with different symbol formats
        test_cases = [
            ("ETH/USDT", "ETH_USDT_1h.parquet"),
            ("BTC/USD", "BTC_USD_1h.parquet"),
            ("DOGE/BTC", "DOGE_BTC_1h.parquet"),
        ]
        
        for symbol, expected_filename in test_cases:
            test_settings.SYMBOL = symbol
            feed = PriceFeed(client=mock_exchange_client, settings=test_settings)
            cache_path = feed._cache_path()
            assert cache_path.name == expected_filename
