"""
Unit tests for MarketDataCache pre-loading functionality.
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from bot_v2.execution.market_data_cache import MarketDataCache


class TestMarketDataCachePreload:
    """Test cases for cache pre-loading functionality."""

    @pytest.fixture
    def cache(self):
        """Create a fresh cache instance for each test with disk cache disabled."""
        return MarketDataCache(default_ttl=30, max_size=500, enable_disk_cache=False)

    @pytest.fixture
    def mock_exchange(self):
        """Create a mock exchange with fetch_ticker and fetch_ohlcv methods."""
        exchange = MagicMock()

        # Mock ticker data
        ticker_data = {
            "symbol": "BTCUSDT",
            "last": 50000.0,
            "bid": 49990.0,
            "ask": 50010.0,
        }
        exchange.fetch_ticker = AsyncMock(return_value=ticker_data)

        # Mock OHLCV data (timestamp, open, high, low, close, volume)
        ohlcv_data = [
            [1640995200000, 47000.0, 48000.0, 46500.0, 47500.0, 100.0],  # 2022-01-01
            [1641081600000, 47500.0, 48500.0, 47000.0, 48000.0, 110.0],  # 2022-01-02
        ]
        exchange.fetch_ohlcv = AsyncMock(return_value=ohlcv_data)

        return exchange

    @pytest.mark.asyncio
    async def test_preload_symbols_success(self, cache, mock_exchange):
        """Test successful pre-loading of multiple symbols."""
        symbols = ["BTC/USDT", "ETH/USDT"]

        await cache.preload_symbols(symbols, mock_exchange)

        # Verify ticker calls
        assert mock_exchange.fetch_ticker.call_count == 2
        mock_exchange.fetch_ticker.assert_any_call("BTCUSDT")
        mock_exchange.fetch_ticker.assert_any_call("ETHUSDT")

        # Verify OHLCV calls
        assert mock_exchange.fetch_ohlcv.call_count == 2
        mock_exchange.fetch_ohlcv.assert_any_call("BTCUSDT", timeframe="1h", limit=100)
        mock_exchange.fetch_ohlcv.assert_any_call("ETHUSDT", timeframe="1h", limit=100)

        # Verify cache has price data
        btc_price = cache.get_price("BTC/USDT")
        assert btc_price == Decimal("50000.0")

        eth_price = cache.get_price("ETH/USDT")
        assert eth_price == Decimal("50000.0")  # Same mock data

        # Verify cache has OHLCV data
        btc_ohlcv = cache.get_ohlcv("BTC/USDT", "1h", 100)
        assert btc_ohlcv is not None
        assert len(btc_ohlcv) == 2
        assert isinstance(btc_ohlcv, pd.DataFrame)
        assert btc_ohlcv.index.name == "timestamp"

    @pytest.mark.asyncio
    async def test_preload_symbols_empty_list(self, cache, mock_exchange):
        """Test pre-loading with empty symbol list."""
        await cache.preload_symbols([], mock_exchange)

        # No calls should be made
        mock_exchange.fetch_ticker.assert_not_called()
        mock_exchange.fetch_ohlcv.assert_not_called()

    @pytest.mark.asyncio
    async def test_preload_symbols_partial_failure(self, cache, mock_exchange):
        """Test pre-loading when some symbols fail."""
        # Make ETH fetch fail
        mock_exchange.fetch_ticker.side_effect = lambda symbol: (
            {"last": 50000.0} if symbol == "BTCUSDT" else Exception("API Error")
        )
        mock_exchange.fetch_ohlcv.side_effect = lambda symbol, **kwargs: (
            [[1640995200000, 47000.0, 48000.0, 46500.0, 47500.0, 100.0]]
            if symbol == "BTCUSDT"
            else Exception("API Error")
        )

        symbols = ["BTC/USDT", "ETH/USDT"]

        # Should not raise exception, but log warnings
        await cache.preload_symbols(symbols, mock_exchange)

        # BTC should be cached
        btc_price = cache.get_price("BTC/USDT")
        assert btc_price == Decimal("50000.0")

        # ETH should not be cached due to error
        eth_price = cache.get_price("ETH/USDT")
        assert eth_price is None

    @pytest.mark.asyncio
    async def test_preload_symbols_ticker_missing_last(self, cache, mock_exchange):
        """Test pre-loading when ticker data doesn't have 'last' price."""
        mock_exchange.fetch_ticker.return_value = {
            "bid": 49990.0,
            "ask": 50010.0,
        }  # No 'last'

        symbols = ["BTC/USDT"]

        await cache.preload_symbols(symbols, mock_exchange)

        # Price should not be cached
        price = cache.get_price("BTC/USDT")
        assert price is None

        # But OHLCV should still be cached
        ohlcv = cache.get_ohlcv("BTC/USDT", "1h", 100)
        assert ohlcv is not None

    @pytest.mark.asyncio
    async def test_preload_symbols_ohlcv_empty(self, cache, mock_exchange):
        """Test pre-loading when OHLCV returns empty data."""
        mock_exchange.fetch_ohlcv.return_value = []

        symbols = ["BTC/USDT"]

        await cache.preload_symbols(symbols, mock_exchange)

        # Price should be cached
        price = cache.get_price("BTC/USDT")
        assert price == Decimal("50000.0")

        # OHLCV should not be cached
        ohlcv = cache.get_ohlcv("BTC/USDT", "1h", 100)
        assert ohlcv is None

    @pytest.mark.asyncio
    async def test_preload_symbols_concurrent_execution(self, cache, mock_exchange):
        """Test that symbols are fetched concurrently."""

        # Add delay to verify concurrent execution
        async def delayed_fetch_ticker(symbol):
            await asyncio.sleep(0.01)  # Small delay
            return {"last": 50000.0}

        async def delayed_fetch_ohlcv(symbol, **kwargs):
            await asyncio.sleep(0.01)  # Small delay
            return [[1640995200000, 47000.0, 48000.0, 46500.0, 47500.0, 100.0]]

        mock_exchange.fetch_ticker = delayed_fetch_ticker
        mock_exchange.fetch_ohlcv = delayed_fetch_ohlcv

        symbols = ["BTC/USDT", "ETH/USDT", "ADA/USDT"]

        start_time = asyncio.get_event_loop().time()
        await cache.preload_symbols(symbols, mock_exchange)
        end_time = asyncio.get_event_loop().time()

        # Should complete faster than sequential (3 symbols * 2 calls * 0.01s = 0.06s)
        duration = end_time - start_time
        assert duration < 0.1  # Allow some overhead

        # All symbols should be cached
        for symbol in symbols:
            price = cache.get_price(symbol)
            assert price == Decimal("50000.0")
            ohlcv = cache.get_ohlcv(symbol, "1h", 100)
            assert ohlcv is not None
