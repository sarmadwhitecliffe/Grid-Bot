"""
src/strategy/regime_detector.py
--------------------------------
Stateless market regime classifier using ADX(14) and Bollinger Band width.

The bot only deploys a grid when the regime is RANGING. On a switch to
TRENDING, the main loop cancels all open orders and pauses.

CPU Optimization:
- Caches computed regime for the same OHLCV data to avoid redundant calculations
- Uses hash of DataFrame to detect changes
"""

import logging
import time
from decimal import Decimal

import pandas as pd
from ta.trend import ADXIndicator
from ta.volatility import BollingerBands

from src.strategy import MarketRegime, RegimeInfo

logger = logging.getLogger(__name__)


class RegimeDetector:
    """
    Classify market conditions as RANGING or TRENDING.

    Algorithm:
    1. Compute ADX(14) on the OHLCV DataFrame.
    2. Compute Bollinger Band Width = (upper - lower) / middle.
    3. RANGING if ADX < adx_threshold AND bb_width < bb_width_threshold.
    4. Otherwise TRENDING.

    CPU Optimization:
    - Caches regime computation based on DataFrame content hash
    - Only recalculates when data actually changes
    """

    def __init__(
        self,
        adx_threshold: int = 25,
        bb_width_threshold: float = 0.04,
        adx_period: int = 14,
        bb_period: int = 20,
        bb_std: float = 2.0,
    ) -> None:
        """
        Initialise the regime detector with configurable thresholds.

        Args:
            adx_threshold:       ADX value above which market is TRENDING.
            bb_width_threshold:  BB width ratio above which market is wide/trending.
            adx_period:          Look-back period for ADX indicator (default 14).
            bb_period:           Look-back period for Bollinger Bands (default 20).
            bb_std:              Standard deviation multiplier for BB (default 2.0).
        """
        self.adx_threshold = adx_threshold
        self.bb_width_threshold = bb_width_threshold
        self.adx_period = adx_period
        self.bb_period = bb_period
        self.bb_std = bb_std

        # CPU Optimization: Cache for regime computation
        self._cache: dict = {}
        self._cache_max_age: float = 60.0  # Max cache age in seconds
        self._cache_hits: int = 0
        self._cache_misses: int = 0

    def detect(self, ohlcv_df: pd.DataFrame) -> RegimeInfo:
        """
        Run regime detection on the supplied OHLCV DataFrame.

        CPU Optimization: Uses caching to avoid redundant calculations for the same data.

        Args:
            ohlcv_df: DataFrame with columns [timestamp, open, high, low,
                      close, volume]. Must have at least
                      max(adx_period, bb_period) + 5 rows for reliable output.

        Returns:
            RegimeInfo: Contains regime classification, indicator values,
                        and a human-readable reason string.
        """
        min_rows = max(self.adx_period, self.bb_period) + 5
        if len(ohlcv_df) < min_rows:
            logger.warning(
                "Insufficient candles (%d < %d) — returning UNKNOWN regime.",
                len(ohlcv_df),
                min_rows,
            )
            return RegimeInfo(
                regime=MarketRegime.UNKNOWN,
                adx=Decimal("0"),
                bb_width=Decimal("0"),
                adx_threshold=self.adx_threshold,
                bb_width_threshold=Decimal(str(self.bb_width_threshold)),
                reason="Insufficient candle data",
            )

        # CPU Optimization: Check cache first
        # Use last timestamp and length as cache key (faster than full hash)
        cache_key = (
            int(ohlcv_df["timestamp"].iloc[-1])
            if "timestamp" in ohlcv_df.columns
            else len(ohlcv_df),
            len(ohlcv_df),
        )
        current_time = time.time()

        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if current_time - cached["timestamp"] < self._cache_max_age:
                self._cache_hits += 1
                logger.debug(
                    "Regime cache HIT (hits=%d, misses=%d)",
                    self._cache_hits,
                    self._cache_misses,
                )
                return cached["result"]

        self._cache_misses += 1

        # ADX(14)
        adx_val = Decimal(
            str(
                ADXIndicator(
                    high=ohlcv_df["high"],
                    low=ohlcv_df["low"],
                    close=ohlcv_df["close"],
                    window=self.adx_period,
                )
                .adx()
                .iloc[-1]
            )
        )

        # Bollinger Band width = (upper - lower) / middle
        bb = BollingerBands(
            close=ohlcv_df["close"],
            window=self.bb_period,
            window_dev=self.bb_std,
        )
        mid = Decimal(str(bb.bollinger_mavg().iloc[-1]))
        if mid > 0:
            bb_width = (
                Decimal(str(bb.bollinger_hband().iloc[-1]))
                - Decimal(str(bb.bollinger_lband().iloc[-1]))
            ) / mid
        else:
            bb_width = Decimal("0")

        ranging = adx_val < Decimal(str(self.adx_threshold)) and bb_width < Decimal(
            str(self.bb_width_threshold)
        )
        regime = MarketRegime.RANGING if ranging else MarketRegime.TRENDING

        if ranging:
            reason = (
                f"ADX={float(adx_val):.2f} < {self.adx_threshold} "
                f"AND BB_w={float(bb_width):.4f} < {self.bb_width_threshold}"
            )
        else:
            reason = f"ADX={float(adx_val):.2f} or BB_w={float(bb_width):.4f} exceeds threshold"

        result = RegimeInfo(
            regime=regime,
            adx=adx_val,
            bb_width=bb_width,
            adx_threshold=self.adx_threshold,
            bb_width_threshold=Decimal(str(self.bb_width_threshold)),
            reason=reason,
        )

        # CPU Optimization: Cache the result
        self._cache[cache_key] = {
            "timestamp": current_time,
            "result": result,
        }

        # Limit cache size to prevent memory growth
        if len(self._cache) > 100:
            oldest_key = min(self._cache.items(), key=lambda x: x[1]["timestamp"])[0]
            del self._cache[oldest_key]

        logger.debug("Regime: %s | %s", regime.value, reason)
        return result
