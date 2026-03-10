from typing import List

import numpy as np
import pandas as pd


class VolatilityEstimator:
    """
    Calculates various volatility metrics for a given OHLCV dataset.
    Supported metrics:
    - ATR% (Average True Range as percentage of price)
    - StdDev (Standard Deviation of returns)
    - Parkinson (High-Low range based volatility)
    - EWMA (Exponentially Weighted Moving Average of returns)
    """

    @staticmethod
    def calculate_atr_pct(ohlcv: pd.DataFrame, period: int = 14) -> float:
        """
        Calculate Average True Range Percentage (ATR%).
        ATR% = (ATR / Close) * 100
        """
        if len(ohlcv) < period + 1:
            return 0.0

        high = ohlcv["high"]
        low = ohlcv["low"]
        close = ohlcv["close"]
        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()

        # Get the last ATR value
        last_atr = atr.iloc[-1]
        last_close = close.iloc[-1]

        if last_close == 0:
            return 0.0

        return (last_atr / last_close) * 100.0

    @staticmethod
    def calculate_stddev_returns(ohlcv: pd.DataFrame, period: int = 14) -> float:
        """
        Calculate Standard Deviation of Returns.
        """
        if len(ohlcv) < period:
            return 0.0

        close = ohlcv["close"]
        returns = close.pct_change()
        std_dev = returns.rolling(window=period).std()

        return std_dev.iloc[-1] * 100.0  # Return as percentage

    @staticmethod
    def calculate_parkinson(ohlcv: pd.DataFrame, period: int = 14) -> float:
        """
        Calculate Parkinson Volatility (High-Low based).
        """
        if len(ohlcv) < period:
            return 0.0

        high = ohlcv["high"]
        low = ohlcv["low"]

        # Parkinson estimator formula: sqrt(1/(4*ln(2)) * sum(ln(H/L)^2) / N)
        # We calculate it over a rolling window

        log_hl_ratio_sq = (np.log(high / low)) ** 2
        sum_sq = log_hl_ratio_sq.rolling(window=period).sum()

        const = 1.0 / (4.0 * np.log(2.0))
        parkinson_var = const * sum_sq / period
        parkinson_vol = np.sqrt(parkinson_var)

        return parkinson_vol.iloc[-1] * 100.0  # Return as percentage

    @staticmethod
    def calculate_ewma_volatility(ohlcv: pd.DataFrame, span: int = 14) -> float:
        """
        Calculate EWMA Volatility of returns.
        """
        if len(ohlcv) < span:
            return 0.0

        close = ohlcv["close"]
        returns = close.pct_change()

        # EWMA of squared returns (assuming mean return is 0 for short periods)
        # or standard deviation
        ewm_std = returns.ewm(span=span).std()

        return ewm_std.iloc[-1] * 100.0  # Return as percentage

    @staticmethod
    def get_volatility(
        ohlcv_data: List[List[float]], method: str = "atr_pct", period: int = 14
    ) -> float:
        """
        Main entry point to calculate volatility.
        ohlcv_data: List of [timestamp, open, high, low, close, volume]
        """
        if not ohlcv_data or len(ohlcv_data) < period:
            return 0.0

        df = pd.DataFrame(
            ohlcv_data, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

        if method == "atr_pct":
            return VolatilityEstimator.calculate_atr_pct(df, period)
        elif method == "stddev":
            return VolatilityEstimator.calculate_stddev_returns(df, period)
        elif method == "parkinson":
            return VolatilityEstimator.calculate_parkinson(df, period)
        elif method == "ewma":
            return VolatilityEstimator.calculate_ewma_volatility(df, period)
        else:
            raise ValueError(f"Unknown volatility method: {method}")
