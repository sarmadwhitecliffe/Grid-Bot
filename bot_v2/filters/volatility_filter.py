"""
Volatility filter for entry signals.

Prevents trading in low-volatility conditions where edge doesn't exist.
"""

import asyncio
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Tuple

import pandas as pd

from bot_v2.models.strategy_config import StrategyConfig

logger = logging.getLogger(__name__)


class VolatilityFilter:
    """
    Adaptive volatility filter with caching.

    Compares current ATR% to historical percentile threshold with caching
    to avoid repeated OHLCV fetches.
    """

    def __init__(self):
        """Initialize volatility filter with empty cache."""
        self._atr_cache: Dict[Tuple, Dict[str, Any]] = {}
        self._atr_cache_lock = asyncio.Lock()

    async def is_volatile_enough(
        self,
        symbol: str,
        config: StrategyConfig,
        current_price: Decimal,
        current_atr: Decimal,
        exchange,  # ExchangeInterface
    ) -> bool:
        """
        Check if volatility is sufficient for trading (EXACT from bot_v1).

        Passes if current ATR% >= chosen percentile threshold OR >= absolute floor.

        Args:
            symbol: Symbol identifier
            config: Strategy configuration
            current_price: Current price as Decimal
            current_atr: Current ATR value as Decimal
            exchange: Exchange interface for fetching OHLCV data

        Returns:
            True if volatility check passes, False otherwise
        """
        # Configurable strategy params with Decimal precision
        try:
            threshold_percentile_raw = getattr(
                config, "volatility_threshold_percentile", Decimal("0.0")
            )
            # Convert Decimal to int for percentile (0-100)
            threshold_percentile = (
                int(float(threshold_percentile_raw))
                if threshold_percentile_raw > Decimal("0")
                else 0
            )

            min_atr_percent = Decimal(str(getattr(config, "min_atr_percent", "0.0")))
            cache_ttl = getattr(config, "volatility_cache_ttl", 300)

            if not (0 <= threshold_percentile <= 100):
                logger.warning(
                    f"[{symbol}] Invalid threshold_percentile {threshold_percentile}, using 0"
                )
                threshold_percentile = 0

            if cache_ttl <= 0:
                logger.warning(f"[{symbol}] Invalid cache_ttl {cache_ttl}, using 300")
                cache_ttl = 300

        except (ValueError, InvalidOperation) as e:
            logger.warning(
                f"[{symbol}] Error parsing strategy parameters: {e}. Using defaults."
            )
            threshold_percentile = 0
            min_atr_percent = Decimal("0.0")
            cache_ttl = 300

        # Minimum data requirements
        try:
            min_data_points = max(int(config.atr_period * 1.5), 15)
        except (AttributeError, TypeError) as e:
            logger.warning(
                f"[{symbol}] Error accessing config.atr_period: {e}. Using default."
            )
            min_data_points = 30

        # Cache key
        try:
            cache_key = (
                symbol,
                config.timeframe,
                config.volatility_filter_lookback,
                config.atr_period,
                threshold_percentile,
                str(min_atr_percent),
            )
        except (AttributeError, TypeError) as e:
            logger.error(
                f"[{symbol}] Error creating cache key: {e}. Allowing trade by default."
            )
            return True

        now = time.monotonic()

        # Thread-safe cache access
        try:
            async with self._atr_cache_lock:
                cached = self._atr_cache.get(cache_key)
                cache_hit = cached and (now - cached["timestamp"]) < cache_ttl

                if cache_hit:
                    ref_atr_ratio = cached["ref_atr_ratio"]
                    atr_ratio_series = cached["atr_ratio_series"]
                    logger.debug(
                        f"[{symbol}] Using cached volatility data (age: {now - cached['timestamp']:.1f}s)"
                    )
                else:
                    # Fetch historical data
                    try:
                        ohlcv_df = await exchange.fetch_ohlcv(
                            symbol, config.timeframe, config.volatility_filter_lookback
                        )

                        if ohlcv_df is None or ohlcv_df.empty:
                            logger.warning(
                                f"[{symbol}] No OHLCV data for volatility filter. Allowing trade by default."
                            )
                            return True

                        df_clean = ohlcv_df.dropna()

                        if len(df_clean) < min_data_points:
                            logger.warning(
                                f"[{symbol}] Insufficient data for ATR calculation "
                                f"({len(df_clean)} < {min_data_points}). Allowing trade by default."
                            )
                            return True

                        logger.info(
                            f"[{symbol}] Processing {len(df_clean)} data points for volatility analysis"
                        )

                        # Calculate True Range components
                        try:
                            df_clean["high_low"] = df_clean["high"] - df_clean["low"]
                            df_clean["high_close"] = (
                                df_clean["high"] - df_clean["close"].shift()
                            ).abs()
                            df_clean["low_close"] = (
                                df_clean["low"] - df_clean["close"].shift()
                            ).abs()
                            df_clean["tr"] = df_clean[
                                ["high_low", "high_close", "low_close"]
                            ].max(axis=1)

                            # ATR% series calculation
                            atr_series = (
                                df_clean["tr"]
                                .ewm(span=config.atr_period, adjust=False)
                                .mean()
                            )
                            atr_ratio_series = (atr_series / df_clean["close"]) * 100.0
                            atr_ratio_series = atr_ratio_series.dropna()

                            if atr_ratio_series.empty:
                                logger.warning(
                                    f"[{symbol}] ATR ratio series empty. Allowing trade by default."
                                )
                                return True

                            # Calculate threshold based on percentile
                            try:
                                if (
                                    threshold_percentile == 0
                                    or threshold_percentile == 50
                                ):
                                    ref_atr_ratio = float(atr_ratio_series.median())
                                else:
                                    ref_atr_ratio = float(
                                        atr_ratio_series.quantile(
                                            threshold_percentile / 100.0
                                        )
                                    )

                                if pd.isna(ref_atr_ratio) or ref_atr_ratio <= 0:
                                    logger.warning(
                                        f"[{symbol}] Invalid threshold: {ref_atr_ratio}. Using mean."
                                    )
                                    ref_atr_ratio = float(atr_ratio_series.mean())

                            except Exception as e:
                                logger.warning(
                                    f"[{symbol}] Error calculating threshold: {e}. Using mean."
                                )
                                ref_atr_ratio = float(atr_ratio_series.mean())

                            # Update cache
                            try:
                                self._atr_cache[cache_key] = {
                                    "timestamp": now,
                                    "ref_atr_ratio": ref_atr_ratio,
                                    "atr_ratio_series": atr_ratio_series,
                                }
                                logger.debug(
                                    f"[{symbol}] Cached volatility data: ref_atr_ratio={ref_atr_ratio:.4f}"
                                )
                            except Exception as e:
                                logger.warning(
                                    f"[{symbol}] Failed to update cache: {e}"
                                )

                        except Exception as e:
                            logger.error(
                                f"[{symbol}] Error during ATR calculation: {e}. Allowing trade."
                            )
                            return True
                    except Exception as e:
                        logger.error(
                            f"[{symbol}] Error fetching OHLCV data: {e}. Allowing trade by default."
                        )
                        return True
        except Exception as e:
            logger.error(
                f"[{symbol}] Critical error in cache access: {e}. Allowing trade by default."
            )
            return True

        # Calculate current ATR percentage
        try:
            current_atr_ratio = float((current_atr / current_price) * Decimal("100"))
        except (InvalidOperation, ZeroDivisionError) as e:
            logger.error(
                f"[{symbol}] Error calculating current ATR ratio: {e}. Allowing trade."
            )
            return True

        # Apply floor and threshold
        try:
            effective_threshold = max(ref_atr_ratio, float(min_atr_percent))

            if current_atr_ratio >= effective_threshold:
                logger.info(
                    f"[{symbol}] Volatility filter PASSED. ATR%={current_atr_ratio:.4f}, "
                    f"Threshold={ref_atr_ratio:.4f} (p{threshold_percentile}), "
                    f"Floor={float(min_atr_percent):.2f}%, Effective={effective_threshold:.4f}"
                )
                return True

            logger.warning(
                f"[{symbol}] Trade REJECTED by volatility filter. ATR%={current_atr_ratio:.4f}, "
                f"Threshold={ref_atr_ratio:.4f} (p{threshold_percentile}), "
                f"Floor={float(min_atr_percent):.2f}%, Effective={effective_threshold:.4f}"
            )
            return False
        except Exception as e:
            logger.error(
                f"[{symbol}] Error in final decision logic: {e}. Allowing trade by default."
            )
            return True
