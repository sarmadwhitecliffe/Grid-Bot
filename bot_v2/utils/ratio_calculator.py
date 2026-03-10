"""
Ratio Calculator Module

Provides standardized ratio calculation methods for consistent MFE/MAE calculations
across the trading system. Eliminates inconsistent ratio semantics between different
components.

Key Features:
- Entry ratio: MFE/MAE from position entry
- Post-TP1 ratio: Quality assessment after partial close
- Effective ratio: Context-aware ratio selection for trailing
- Comprehensive validation and bounds checking
- Clear documentation of ratio semantics and edge cases

Usage Example:
---------------
from bot_v2.utils.ratio_calculator import RatioCalculator
from bot_v2.models.position import Position

# Create a Position object (example values)
pos = Position(entry_price=100, mfe=5, mae=1, tp1a_hit=True,
               peak_favorable_r_beyond_tp1=0.08, max_adverse_r_since_tp1_post=0.02)

# Calculate entry ratio
entry_result = RatioCalculator.entry_ratio(pos)
print(f"Entry Ratio: {entry_result.ratio}, Description: {entry_result.description}")

# Calculate post-TP1 ratio
post_tp1_result = RatioCalculator.post_tp1_ratio(pos)
print(f"Post-TP1 Ratio: {post_tp1_result.ratio}, Description: {post_tp1_result.description}")

# Get effective ratio for trailing
effective_result = RatioCalculator.effective_ratio(pos)
print(f"Effective Ratio: {effective_result.ratio}, Source: {effective_result.description}")

Refer to the main README and docs/TRAILING_STOP_REFACTORING_PLAN.md for more details.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

from bot_v2.models.position import Position

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RatioResult:
    """Result of a ratio calculation with metadata."""

    ratio: Decimal
    ratio_type: str
    description: str
    is_valid: bool = True
    warning_message: Optional[str] = None


class RatioCalculator:
    """
    Unified ratio calculator providing consistent MFE/MAE calculations.

    This class standardizes ratio calculations across the system, ensuring
    that the same position data produces the same ratio regardless of
    which component performs the calculation.

    Ratio Types:
    - entry_ratio: MFE/MAE from position entry (traditional quality metric)
    - post_tp1_ratio: Post-TP1 quality using separate metrics after partial close
    - effective_ratio: Context-aware selection between entry and post-TP1 ratios
    """

    # Thresholds for special case handling
    PRICE_THRESHOLD_MULTIPLIER = Decimal("0.001")  # 0.1% of entry price
    POST_TP1_FAVORABLE_THRESHOLD = Decimal("0.05")  # 0.05R minimum for assessment
    POST_TP1_ADVERSE_THRESHOLD = Decimal("0.01")  # 0.01R for "extremely clean"
    ENTRY_CLEAN_THRESHOLD = Decimal("0.001")  # 0.1% for entry clean trades

    @staticmethod
    def entry_ratio(position: Position) -> RatioResult:
        """
        Calculate entry ratio (MFE/MAE from position entry).

        This is the traditional quality metric used for most trailing decisions.
        Special cases:
        - Extremely clean (MAE < 0.1% of entry): Capped at 50.0
        - No meaningful movement: Returns 1.0
        - Normal case: MFE / MAE

        Args:
            position: Position object with MFE/MAE data

        Returns:
            RatioResult with calculated ratio and metadata
        """
        price_threshold = RatioCalculator.PRICE_THRESHOLD_MULTIPLIER * abs(
            position.entry_price
        )

        # Extremely clean trade (virtually no adverse movement)
        if position.mae < price_threshold:
            return RatioResult(
                ratio=Decimal("50.0"),
                ratio_type="entry",
                description="extremely clean (MAE < 0.1% of entry price)",
                is_valid=True,
            )

        # No meaningful favorable movement yet
        if position.mfe < price_threshold:
            return RatioResult(
                ratio=Decimal("1.0"),
                ratio_type="entry",
                description="no meaningful movement yet",
                is_valid=True,
            )

        # Normal calculation
        try:
            ratio = position.mfe / position.mae
            return RatioResult(
                ratio=ratio,
                ratio_type="entry",
                description="standard MFE/MAE calculation",
                is_valid=True,
            )
        except (ZeroDivisionError, InvalidOperation) as e:
            logger.warning(
                f"Invalid entry ratio calculation for position {position.symbol}: MFE={position.mfe}, MAE={position.mae}"
            )
            return RatioResult(
                ratio=Decimal("1.0"),
                ratio_type="entry",
                description="fallback due to calculation error",
                is_valid=False,
                warning_message=f"Calculation error: {e}",
            )

    @staticmethod
    def entry_ratio_from_values(
        mfe: Decimal, mae: Decimal, entry_price: Decimal
    ) -> RatioResult:
        """
        Calculate entry ratio from individual MFE/MAE values.

        This is a convenience method for cases where you don't have a full Position object.

        Args:
            mfe: Maximum favorable excursion
            mae: Maximum adverse excursion
            entry_price: Entry price for threshold calculations

        Returns:
            RatioResult with calculated ratio and metadata
        """
        price_threshold = RatioCalculator.PRICE_THRESHOLD_MULTIPLIER * abs(entry_price)

        # Extremely clean trade (virtually no adverse movement)
        if mae < price_threshold:
            return RatioResult(
                ratio=Decimal("50.0"),
                ratio_type="entry",
                description="extremely clean (MAE < 0.1% of entry price)",
                is_valid=True,
            )

        # No meaningful favorable movement yet
        if mfe < price_threshold:
            return RatioResult(
                ratio=Decimal("1.0"),
                ratio_type="entry",
                description="no meaningful movement yet",
                is_valid=True,
            )

        # Normal calculation
        try:
            ratio = mfe / mae
            return RatioResult(
                ratio=ratio,
                ratio_type="entry",
                description="standard MFE/MAE calculation",
                is_valid=True,
            )
        except (ZeroDivisionError, InvalidOperation) as e:
            logger.warning(f"Invalid entry ratio calculation: MFE={mfe}, MAE={mae}")
            return RatioResult(
                ratio=Decimal("1.0"),
                ratio_type="entry",
                description="fallback due to calculation error",
                is_valid=False,
                warning_message=f"Calculation error: {e}",
            )

    @staticmethod
    def post_tp1_ratio_from_values(
        favorable_r: Decimal, adverse_r: Decimal
    ) -> RatioResult:
        """
        Calculate post-TP1 quality ratio from individual R-multiple values.

        This is a convenience method for cases where you don't have a full Position object.

        Args:
            favorable_r: Peak favorable R beyond TP1
            adverse_r: Maximum adverse R since TP1

        Returns:
            RatioResult with calculated ratio and metadata
        """
        # Too early to assess (insufficient favorable movement)
        if favorable_r < RatioCalculator.POST_TP1_FAVORABLE_THRESHOLD:
            return RatioResult(
                ratio=Decimal("1.0"),
                ratio_type="post_tp1",
                description="too early to assess (favorable < 0.05R)",
                is_valid=True,
            )

        # Extremely clean (virtually no adverse movement)
        if adverse_r < RatioCalculator.POST_TP1_ADVERSE_THRESHOLD:
            return RatioResult(
                ratio=Decimal("50.0"),
                ratio_type="post_tp1",
                description="extremely clean (adverse < 0.01R)",
                is_valid=True,
            )

        # Normal calculation
        try:
            ratio = favorable_r / adverse_r
            return RatioResult(
                ratio=ratio,
                ratio_type="post_tp1",
                description="standard post-TP1 calculation",
                is_valid=True,
            )
        except (ZeroDivisionError, InvalidOperation) as e:
            logger.warning(
                f"Invalid post-TP1 ratio calculation: favorable={favorable_r}, adverse={adverse_r}"
            )
            return RatioResult(
                ratio=Decimal("1.0"),
                ratio_type="post_tp1",
                description="fallback due to calculation error",
                is_valid=False,
                warning_message=f"Calculation error: {e}",
            )

    @staticmethod
    def post_tp1_ratio(position: Position) -> RatioResult:
        """
        Calculate post-TP1 quality ratio using separate metrics.

        Used after TP1a partial close (30% at 0.7 ATR) to assess quality
        based on movement since the partial close.
        Special cases:
        - Extremely clean (adverse < 0.01R): Capped at 50.0
        - Too early to assess (favorable < 0.05R): Returns 1.0
        - Normal case: peak_favorable_r_beyond_tp1 / max_adverse_r_since_tp1_post

        Args:
            position: Position object with post-TP1 metrics

        Returns:
            RatioResult with calculated ratio and metadata
        """
        # Check if we have post-TP1 data
        if not position.tp1a_hit:
            return RatioResult(
                ratio=Decimal("1.0"),
                ratio_type="post_tp1",
                description="no TP1a hit yet",
                is_valid=True,
            )

        favorable_r = position.peak_favorable_r_beyond_tp1
        adverse_r = position.max_adverse_r_since_tp1_post

        # Too early to assess (insufficient favorable movement)
        if favorable_r < RatioCalculator.POST_TP1_FAVORABLE_THRESHOLD:
            return RatioResult(
                ratio=Decimal("1.0"),
                ratio_type="post_tp1",
                description="too early to assess (favorable < 0.05R)",
                is_valid=True,
            )

        # Extremely clean (virtually no adverse movement)
        if adverse_r < RatioCalculator.POST_TP1_ADVERSE_THRESHOLD:
            return RatioResult(
                ratio=Decimal("50.0"),
                ratio_type="post_tp1",
                description="extremely clean (adverse < 0.01R)",
                is_valid=True,
            )

        # Normal calculation
        try:
            ratio = favorable_r / adverse_r
            return RatioResult(
                ratio=ratio,
                ratio_type="post_tp1",
                description="standard post-TP1 calculation",
                is_valid=True,
            )
        except (ZeroDivisionError, InvalidOperation) as e:
            logger.warning(
                f"Invalid post-TP1 ratio calculation for position {position.symbol}: favorable={favorable_r}, adverse={adverse_r}"
            )
            return RatioResult(
                ratio=Decimal("1.0"),
                ratio_type="post_tp1",
                description="fallback due to calculation error",
                is_valid=False,
                warning_message=f"Calculation error: {e}",
            )

    @staticmethod
    def effective_ratio(position: Position) -> RatioResult:
        """
        Get effective ratio for trailing decisions.

        Context-aware ratio selection:
        - If TP1a hit and post-TP1 data available: Use post-TP1 ratio
        - Otherwise: Use entry ratio

        This ensures trailing decisions use the most relevant quality assessment
        for the current position state.

        Args:
            position: Position object

        Returns:
            RatioResult with the most appropriate ratio for trailing
        """
        # Use post-TP1 ratio if available and meaningful
        if (
            position.tp1a_hit
            and position.peak_favorable_r_beyond_tp1
            > RatioCalculator.POST_TP1_FAVORABLE_THRESHOLD
        ):
            post_tp1_result = RatioCalculator.post_tp1_ratio(position)
            if post_tp1_result.is_valid:
                return post_tp1_result

        # Fallback to entry ratio
        return RatioCalculator.entry_ratio(position)

    @staticmethod
    def get_ratio_for_trailing(position: Position) -> Tuple[Decimal, str]:
        """
        Legacy method for backward compatibility.

        Returns the effective ratio and its source description.
        This method maintains the existing API while using the standardized calculator.

        Args:
            position: Position object

        Returns:
            Tuple of (ratio_value, ratio_source_description)
        """
        result = RatioCalculator.effective_ratio(position)
        return result.ratio, result.description
