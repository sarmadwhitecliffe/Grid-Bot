import unittest

import pandas as pd

from bot_v2.signals.ny_breakout_buffer import DynamicBufferManager
from bot_v2.utils.volatility_estimator import VolatilityEstimator


class TestVolatilityEstimator(unittest.TestCase):
    def setUp(self):
        # Create synthetic OHLCV data
        # 20 days of data
        import random

        random.seed(42)
        self.data = []
        price = 100.0
        for i in range(20):
            change = 1.0 + (random.random() - 0.5) * 0.04  # +/- 2%
            open_p = price
            close = price * change
            high = max(open_p, close) * 1.01
            low = min(open_p, close) * 0.99
            volume = 1000
            self.data.append([i, open_p, high, low, close, volume])
            price = close

        self.df = pd.DataFrame(
            self.data, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

    def test_atr_pct(self):
        atr_pct = VolatilityEstimator.calculate_atr_pct(self.df, period=14)
        self.assertIsInstance(atr_pct, float)
        self.assertGreater(atr_pct, 0.0)
        # Rough check: TR is about 4% of price (1.02 - 0.98 = 0.04 relative to 1.00 approx)
        # So ATR% should be around 4%
        self.assertTrue(2.0 < atr_pct < 6.0)

    def test_stddev_returns(self):
        stddev = VolatilityEstimator.calculate_stddev_returns(self.df, period=14)
        self.assertIsInstance(stddev, float)
        self.assertGreater(stddev, 0.0)

    def test_parkinson(self):
        parkinson = VolatilityEstimator.calculate_parkinson(self.df, period=14)
        self.assertIsInstance(parkinson, float)
        self.assertGreater(parkinson, 0.0)

    def test_ewma(self):
        ewma = VolatilityEstimator.calculate_ewma_volatility(self.df, span=14)
        self.assertIsInstance(ewma, float)
        self.assertGreater(ewma, 0.0)


class TestDynamicBufferManager(unittest.TestCase):
    def setUp(self):
        self.config = {
            "enabled": True,
            "base_buffer_pct": 0.5,
            "volatility_multiplier": 0.5,
            "min_buffer_pct": 0.1,
            "max_buffer_pct": 5.0,
            "estimator_method": "atr_pct",
            "estimator_period": 14,
            "symbol_overrides": {
                "TEST/USDT": {"base_buffer_pct": 1.0, "volatility_multiplier": 1.0}
            },
        }
        self.manager = DynamicBufferManager(self.config)

        # Synthetic data
        self.data = []
        price = 100.0
        for i in range(20):
            self.data.append([i, price, price * 1.02, price * 0.98, price * 1.01, 1000])
            price *= 1.01

    def test_calculate_buffer_default(self):
        # Volatility (ATR%) approx 4%
        # Buffer = 0.5 + 0.5 * 4 = 2.5%
        buffer = self.manager.calculate_buffer("BTC/USDT", self.data)
        self.assertTrue(2.0 < buffer < 3.0)

    def test_calculate_buffer_override(self):
        # Override: base=1.0, k=1.0
        # Buffer = 1.0 + 1.0 * 4 = 5.0%
        # Clamped to max 5.0
        buffer = self.manager.calculate_buffer("TEST/USDT", self.data)
        self.assertAlmostEqual(buffer, 5.0, delta=0.5)

    def test_disabled(self):
        self.manager.enabled = False
        buffer = self.manager.calculate_buffer("BTC/USDT", self.data)
        self.assertEqual(buffer, 0.0)

    def test_clamping(self):
        # Set max to 1.0
        self.manager.default_max = 1.0
        buffer = self.manager.calculate_buffer("BTC/USDT", self.data)
        self.assertLessEqual(buffer, 1.0)


if __name__ == "__main__":
    unittest.main()
