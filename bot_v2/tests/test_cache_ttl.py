import time
from unittest.mock import patch

from bot_v2.execution.market_data_cache import MarketDataCache


def test_cache_ttl_config():
    """
    Verify that TTLs are loaded from environment variables.
    """
    with patch.dict(
        "os.environ", {"PRICE_TTL_SECONDS": "5.0", "OHLCV_TTL_SECONDS": "10.0"}
    ):
        cache = MarketDataCache()
        assert cache._price_ttl == 5.0
        assert cache._ohlcv_ttl == 10.0


def test_price_cache_expiry():
    """
    Verify that price cache expires after TTL.
    """
    with patch.dict("os.environ", {"PRICE_TTL_SECONDS": "0.1"}):
        cache = MarketDataCache()
        cache.set_price("BTC/USDT", 50000.0)

        # Immediate check
        assert cache.get_price("BTC/USDT") == 50000.0

        # Wait for expiry
        time.sleep(0.15)

        assert cache.get_price("BTC/USDT") is None

    def test_ohlcv_cache_expiry():
        """
        Verify that OHLCV cache expires after TTL.
        """
        with patch.dict("os.environ", {"OHLCV_TTL_SECONDS": "0.1"}):
            cache = MarketDataCache()
            ohlcv = [[1000, 1, 2, 3, 4, 5]]
            cache.set_ohlcv("BTC/USDT", "1m", 100, ohlcv)

            # Immediate hit
            assert cache.get_ohlcv("BTC/USDT", "1m", 100) == ohlcv

            # Wait for expiry
            time.sleep(0.2)

            # Miss
            assert cache.get_ohlcv("BTC/USDT", "1m", 100) is None
