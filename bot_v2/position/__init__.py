"""
Position management package.

Provides position tracking, MFE/MAE analysis, quality assessment,
and trailing stop logic.
"""

from .tracker import PositionQualityAnalyzer, PositionTracker
from .trailing_stop import TrailingStopCalculator, TrailingStopConfig

__all__ = [
    "PositionTracker",
    "PositionQualityAnalyzer",
    "TrailingStopCalculator",
    "TrailingStopConfig",
]
