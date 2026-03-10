import unittest
from unittest.mock import patch

from bot_v2.signals.ny_breakout_buffer import DynamicBufferManager


class TestBufferLogging(unittest.TestCase):
    def test_buffer_logging_shows_allowed_clamp(self):
        config = {
            "enabled": True,
            "base_buffer_pct": 0.2,
            "volatility_multiplier": 0.05,
            "min_buffer_pct": 0.0,
            "max_buffer_pct": 2.0,
            "estimator_method": "atr_pct",
            "estimator_period": 14,
        }
        dbm = DynamicBufferManager(config)

        fake_ohlcv = [[1, 2, 3, 4, 1234567890]] * 10

        with patch("bot_v2.signals.ny_breakout_buffer.VolatilityEstimator.get_volatility") as mock_vol, patch(
            "bot_v2.signals.ny_breakout_buffer.logger"
        ) as mock_logger:
            mock_vol.return_value = 5.8843

            result = dbm.calculate_buffer("UNI/USDT", fake_ohlcv)

            # Ensure we got a numeric result
            self.assertIsInstance(result, float)

            # Check that logger.info was called and message contains 'Allowed buffer clamp'
            info_calls = [c for c in mock_logger.info.call_args_list]
            self.assertTrue(info_calls, "Expected logger.info to be called")

            # Find a call that contains our 'Allowed buffer clamp' text
            found = False
            for call in info_calls:
                msg = call.args[0]
                if "Allowed buffer clamp" in msg and "%" in msg:
                    found = True
                    break

            self.assertTrue(found, "Expected log message to include 'Allowed buffer clamp' with percent formatting")


if __name__ == "__main__":
    unittest.main()