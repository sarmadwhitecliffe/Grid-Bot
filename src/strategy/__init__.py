"""
src/strategy/__init__.py
------------------------
Shared data classes and enums for the strategy layer.

GridLevel, RegimeInfo, MarketRegime, and GridType are the canonical
data transfer objects exchanged between the regime detector, grid
calculator, and the OMS/risk layers.
"""

from dataclasses import dataclass
from enum import Enum
from decimal import Decimal


class MarketRegime(Enum):
    """Classification of current market conditions."""

    RANGING = "ranging"
    TRENDING = "trending"
    UNKNOWN = "unknown"


class GridType(Enum):
    """Grid spacing mode."""

    ARITHMETIC = "arithmetic"  # Fixed absolute price gap ($)
    GEOMETRIC = "geometric"    # Fixed percentage gap (%)


@dataclass
class RegimeInfo:
    """
    Result of a single regime detection calculation.

    Attributes:
        regime:              Classified market regime.
        adx:                 Latest ADX(14) value.
        bb_width:            Latest Bollinger Band width ratio.
        adx_threshold:       Configured ADX threshold used in this detection.
        bb_width_threshold:  Configured BB width threshold used in this detection.
        reason:              Human-readable explanation of the classification.
    """

    regime: MarketRegime
    adx: Decimal
    bb_width: Decimal
    adx_threshold: int
    bb_width_threshold: Decimal
    reason: str

    @property
    def is_ranging(self) -> bool:
        """Return True if the market is classified as ranging."""
        return self.regime == MarketRegime.RANGING


@dataclass
class GridLevel:
    """
    A single grid limit-order level.

    Attributes:
        price:            Quantized limit price for this level.
        side:             'buy' (below centre) or 'sell' (above centre).
        level_index:      Distance from centre in whole grid steps (1 = closest).
        order_size_quote: USDT capital allocated to this level.
    """

    price: Decimal
    side: str
    level_index: int
    order_size_quote: Decimal
