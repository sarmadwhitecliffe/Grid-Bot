"""
Position performance tracker - MFE/MAE and R-multiple calculations.

This module handles all performance tracking for positions including:
- Maximum Favorable Excursion (MFE)
- Maximum Adverse Excursion (MAE)
- R-multiple tracking (risk-adjusted performance)
- Post-TP1 quality metrics
"""

import logging
from decimal import Decimal
from typing import Tuple

from bot_v2.models.enums import PositionSide, PositionStatus
from bot_v2.models.position import Position
from bot_v2.utils.ratio_calculator import RatioCalculator

logger = logging.getLogger(__name__)


class PositionTracker:
    """
    Tracks position performance metrics (MFE, MAE, R-multiples).

    This class provides methods to update position tracking metrics
    based on current price movements. It maintains Maximum Favorable
    Excursion (MFE), Maximum Adverse Excursion (MAE), and various
    R-multiple based performance indicators.
    """

    @staticmethod
    def update_mfe_mae(position: Position, current_price: Decimal) -> Position:
        """
        Update Maximum Favorable and Adverse Excursion.

        Args:
            position: Current position state
            current_price: Current market price

        Returns:
            Updated position with new MFE/MAE values

        Note:
            - MFE tracks the best unrealized profit achieved
            - MAE tracks the worst unrealized loss experienced
            - Also updates peak_price_since_entry and current_ratio
        """
        entry_price = position.entry_price

        if position.side == PositionSide.LONG:
            # LONG: profit when price goes up, loss when price goes down
            favorable_excursion = current_price - entry_price
            adverse_excursion = entry_price - current_price
            peak_price = max(
                position.peak_price_since_entry or entry_price, current_price
            )
        else:
            # SHORT: profit when price goes down, loss when price goes up
            favorable_excursion = entry_price - current_price
            adverse_excursion = current_price - entry_price
            peak_price = min(
                position.peak_price_since_entry or entry_price, current_price
            )

        # Update MFE if this is the best favorable move seen
        new_mfe = max(position.mfe, favorable_excursion)

        # Update MAE if this is the worst adverse move seen
        new_mae = max(position.mae, adverse_excursion)

        # Calculate current quality ratio (MFE/MAE)
        ratio_result = RatioCalculator.entry_ratio_from_values(
            new_mfe, new_mae, entry_price
        )
        new_ratio = ratio_result.ratio

        # Return updated position
        return position.copy(
            mfe=new_mfe,
            mae=new_mae,
            peak_price_since_entry=peak_price,
            current_ratio=new_ratio,
        )

    @staticmethod
    def update_r_multiples(position: Position, current_price: Decimal) -> Position:
        """
        Update R-multiple tracking (risk-adjusted performance).

        R-multiple measures profit/loss in terms of initial risk:
        - R = 1.0 means profit equal to initial risk
        - R = -1.0 means loss equal to initial risk

        Args:
            position: Current position state
            current_price: Current market price

        Returns:
            Updated position with new R-multiple values
        """
        if position.initial_risk_atr <= 0:
            logger.warning(
                f"{position.symbol_id}: Invalid initial_risk_atr={position.initial_risk_atr}"
            )
            return position

        # Calculate current profit/loss per unit
        if position.side == PositionSide.LONG:
            profit_per_unit = current_price - position.entry_price
        else:
            profit_per_unit = position.entry_price - current_price

        # Convert to R-multiple
        current_r = profit_per_unit / position.initial_risk_atr

        # Update peak favorable R
        peak_favorable_r = max(position.peak_favorable_r, current_r)

        # Update peak adverse R (absolute value of worst negative R)
        adverse_r = abs(min(current_r, Decimal("0")))
        peak_adverse_r = max(position.peak_adverse_r, adverse_r)

        # Track max adverse since entry
        max_adverse_r_since_entry = max(position.max_adverse_r_since_entry, adverse_r)

        # Track max adverse since TP1 (if partially closed)
        max_adverse_r_since_tp1 = position.max_adverse_r_since_tp1
        if position.status == PositionStatus.PARTIALLY_CLOSED:
            max_adverse_r_since_tp1 = max(max_adverse_r_since_tp1, adverse_r)

        return position.copy(
            current_r=current_r,
            peak_favorable_r=peak_favorable_r,
            peak_adverse_r=peak_adverse_r,
            max_adverse_r_since_entry=max_adverse_r_since_entry,
            max_adverse_r_since_tp1=max_adverse_r_since_tp1,
        )

    @staticmethod
    def update_post_tp1_metrics(position: Position, current_price: Decimal) -> Position:
        """
        Update post-TP1 quality tracking metrics.

        After TP1 is hit, we track separate MFE/MAE from the TP1 price
        to assess whether the position continues developing favorably
        or is just chopping around.

        Args:
            position: Current position state (must have tp1a_hit=True)
            current_price: Current market price

        Returns:
            Updated position with post-TP1 metrics
        """
        # Only track if TP1 has been hit and we have the reference price
        if not position.tp1a_hit or not position.tp1a_price:
            return position

        if not position.tp1_ratio_reset_timestamp:
            logger.warning(
                f"{position.symbol_id}: TP1 hit but no ratio reset timestamp"
            )
            return position

        tp1_price = position.tp1a_price

        # Calculate favorable and adverse movement from TP1 price
        if position.side == PositionSide.LONG:
            favorable_from_tp1 = max(current_price - tp1_price, Decimal("0"))
            adverse_from_tp1 = max(tp1_price - current_price, Decimal("0"))
        else:  # SHORT
            favorable_from_tp1 = max(tp1_price - current_price, Decimal("0"))
            adverse_from_tp1 = max(current_price - tp1_price, Decimal("0"))

        # Convert to R-multiples
        favorable_r = favorable_from_tp1 / position.initial_risk_atr
        adverse_r_tp1 = adverse_from_tp1 / position.initial_risk_atr

        # Update peaks since TP1
        peak_favorable_r_beyond_tp1 = max(
            position.peak_favorable_r_beyond_tp1, favorable_r
        )
        max_adverse_r_since_tp1_post = max(
            position.max_adverse_r_since_tp1_post, adverse_r_tp1
        )

        # Calculate ratio since TP1
        ratio_result = RatioCalculator.post_tp1_ratio_from_values(
            peak_favorable_r_beyond_tp1, max_adverse_r_since_tp1_post
        )
        ratio_since_tp1 = ratio_result.ratio

        return position.copy(
            peak_favorable_r_beyond_tp1=peak_favorable_r_beyond_tp1,
            max_adverse_r_since_tp1_post=max_adverse_r_since_tp1_post,
            ratio_since_tp1=ratio_since_tp1,
        )

    @staticmethod
    def calculate_mfe_mae_r_multiples(position: Position) -> Tuple[Decimal, Decimal]:
        """
        Calculate MFE and MAE in R-multiple terms.

        Args:
            position: Position to analyze

        Returns:
            Tuple of (mfe_r, mae_r) - MFE and MAE expressed as R-multiples

        Example:
            >>> mfe_r, mae_r = PositionTracker.calculate_mfe_mae_r_multiples(position)
            >>> if mfe_r > Decimal('2.0') and mae_r < Decimal('0.5'):
            >>>     print("High quality trade: 2R profit with only 0.5R drawdown")
        """
        if position.initial_risk_atr <= 0:
            return Decimal("0"), Decimal("0")

        mfe_r = position.mfe / position.initial_risk_atr
        mae_r = position.mae / position.initial_risk_atr

        return mfe_r, mae_r

    @staticmethod
    def update_all_metrics(
        position: Position, current_price: Decimal, timeframe: str = "30m"
    ) -> Position:
        """
        Update all tracking metrics in one call.

        This is a convenience method that updates MFE/MAE, R-multiples,
        bars_held, and post-TP1 metrics all at once.

        Args:
            position: Current position state
            current_price: Current market price
            timeframe: Trading timeframe (e.g., '30m', '1h') for bars_held calculation

        Returns:
            Position with all metrics updated
        """
        # Update MFE/MAE
        position = PositionTracker.update_mfe_mae(position, current_price)

        # Update R-multiples
        position = PositionTracker.update_r_multiples(position, current_price)

        # Update post-TP1 metrics (if applicable)
        if position.tp1a_hit and position.tp1_ratio_reset_timestamp:
            position = PositionTracker.update_post_tp1_metrics(position, current_price)

        # Update bars_held dynamically (calculate from entry_time and timeframe)
        timeframe_to_seconds = {
            "1m": 60,
            "3m": 180,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "2h": 7200,
            "4h": 14400,
            "1d": 86400,
        }
        timeframe_seconds = timeframe_to_seconds.get(timeframe, 1800)  # Default to 30m

        from datetime import datetime, timezone

        age_seconds = (datetime.now(timezone.utc) - position.entry_time).total_seconds()
        bars = int(age_seconds / timeframe_seconds)
        position = position.copy(bars_held=bars)

        return position

    # === PRIVATE HELPER METHODS ===
    # NOTE: Ratio calculation methods have been moved to RatioCalculator
    # for consistent ratio calculations across the system (Issue 5: Inconsistent Ratio Calculations)
    # - _calculate_current_ratio -> RatioCalculator.entry_ratio_from_values()
    # - _calculate_post_tp1_ratio -> RatioCalculator.post_tp1_ratio_from_values()


class PositionQualityAnalyzer:
    """
    Analyzes position quality based on MFE/MAE ratios.

    Provides methods to classify trade quality and detect
    various quality-related conditions.
    """

    @staticmethod
    def is_high_quality(
        position: Position, threshold: Decimal = Decimal("3.0")
    ) -> bool:
        """
        Check if position shows high quality (strong directional move).

        Args:
            position: Position to analyze
            threshold: Minimum ratio to consider high quality (default: 3.0)

        Returns:
            True if current_ratio >= threshold
        """
        return position.current_ratio >= threshold

    @staticmethod
    def is_low_quality(position: Position, threshold: Decimal = Decimal("1.5")) -> bool:
        """
        Check if position shows low quality (choppy, no clear direction).

        Args:
            position: Position to analyze
            threshold: Maximum ratio to consider low quality (default: 1.5)

        Returns:
            True if current_ratio < threshold
        """
        return position.current_ratio < threshold

    @staticmethod
    def is_weak_post_tp1(
        position: Position, ratio_threshold: Decimal = Decimal("2.0")
    ) -> bool:
        """
        Check if post-TP1 performance is weak (position not developing).

        Args:
            position: Position to analyze
            ratio_threshold: Maximum ratio_since_tp1 to consider weak

        Returns:
            True if TP1 hit and ratio_since_tp1 < threshold
        """
        if not position.tp1a_hit:
            return False
        return position.ratio_since_tp1 < ratio_threshold
