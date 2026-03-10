import logging
from typing import Any, Dict, List

from bot_v2.utils.symbol_utils import normalize_to_config_format
from bot_v2.utils.volatility_estimator import VolatilityEstimator

logger = logging.getLogger(__name__)


class DynamicBufferManager:
    """
    Manages dynamic breakout buffer calculation based on realized volatility.
    """

    def __init__(self, dynamic_buffer_config: Dict[str, Any]):
        self.dynamic_config = dynamic_buffer_config
        self.enabled = self.dynamic_config.get("enabled", False)
        self.default_base = self.dynamic_config.get("base_buffer_pct", 0.0)
        self.default_k = self.dynamic_config.get("volatility_multiplier", 0.0)
        self.default_min = self.dynamic_config.get("min_buffer_pct", 0.0)
        self.default_max = self.dynamic_config.get("max_buffer_pct", 10.0)
        self.method = self.dynamic_config.get("estimator_method", "atr_pct")
        self.period = self.dynamic_config.get("estimator_period", 14)
        self.overrides = self.dynamic_config.get("symbol_overrides", {})

    def calculate_buffer(self, symbol: str, ohlcv_data: List[List[float]]) -> float:
        """
        Calculate the dynamic buffer for a symbol.
        Buffer = base + k * volatility
        Clamped to [min, max]
        """
        if not self.enabled:
            return 0.0

        normalized_symbol = normalize_to_config_format(symbol)

        # Get parameters (check overrides first)
        override = self.overrides.get(normalized_symbol, {})
        base = override.get("base_buffer_pct", self.default_base)
        k = override.get("volatility_multiplier", self.default_k)
        min_buf = override.get("min_buffer_pct", self.default_min)
        max_buf = override.get("max_buffer_pct", self.default_max)

        # Calculate volatility
        try:
            volatility = VolatilityEstimator.get_volatility(
                ohlcv_data, self.method, self.period
            )
        except Exception as e:
            logger.error(f"[{symbol}] Error calculating volatility: {e}")
            return 0.0  # Fallback to 0 buffer on error

        # Calculate raw buffer
        raw_buffer = base + (k * volatility)

        # Clamp
        final_buffer = max(min_buf, min(raw_buffer, max_buf))

        logger.info(
            f"[{symbol}] Dynamic Buffer: {final_buffer:.4f}% (Vol: {volatility:.4f}%, Base: {base}, K: {k}, Allowed buffer clamp: [{min_buf:.2f}%, {max_buf:.2f}%])"
        )

        return final_buffer

    def update_config(self, new_dynamic_buffer_config: Dict[str, Any]):
        """Update configuration (for hot-reload)."""
        self.dynamic_config = new_dynamic_buffer_config
        self.enabled = self.dynamic_config.get("enabled", False)
        self.default_base = self.dynamic_config.get("base_buffer_pct", 0.0)
        self.default_k = self.dynamic_config.get("volatility_multiplier", 0.0)
        self.default_min = self.dynamic_config.get("min_buffer_pct", 0.0)
        self.default_max = self.dynamic_config.get("max_buffer_pct", 10.0)
        self.method = self.dynamic_config.get("estimator_method", "atr_pct")
        self.period = self.dynamic_config.get("estimator_period", 14)
        self.overrides = self.dynamic_config.get("symbol_overrides", {})
