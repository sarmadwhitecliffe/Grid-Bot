"""
PositionState class extracted from old bot.py (proven battle-tested logic)
This is the position tracking and trailing stop logic that has run successfully for 4+ years.
Includes all quality-adjusted trailing, stage-weighted trailing, and post-TP1 logic.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from bot_v2.models.strategy_config import StrategyConfig

logger = logging.getLogger(__name__)


class TradeSide(Enum):
    """Trade direction enumeration."""

    BUY = "buy"
    SELL = "sell"


class PositionStatus(Enum):
    """Position status enumeration."""

    OPEN = "open"
    PARTIALLY_CLOSED = "partially_closed"


class PositionState:
    """Represents the state of an active trading position with full type safety."""

    def __init__(self, **kwargs: Any) -> None:
        # Required core attributes
        self.symbol_id: str = kwargs["symbol_id"]
        self.side: TradeSide = kwargs["side"]
        self.entry_price: Decimal = kwargs["entry_price"]
        self.entry_time: datetime = kwargs["entry_time"]
        self.initial_amount: Decimal = kwargs["initial_amount"]
        self.entry_atr: Decimal = kwargs["entry_atr"]
        self.initial_risk_atr: Decimal = kwargs["initial_risk_atr"]
        self.soft_sl_price: Decimal = kwargs["soft_sl_price"]
        self.hard_sl_price: Decimal = kwargs["hard_sl_price"]
        self.tp1_price: Decimal = kwargs["tp1_price"]

        # Hybrid TP1 system fields
        self.tp1a_price: Optional[Decimal] = kwargs.get(
            "tp1a_price"
        )  # Quick scalp target
        self.tp1a_hit: bool = kwargs.get(
            "tp1a_hit", False
        )  # Track if TP1a already executed

        self.total_entry_fee: Decimal = kwargs["total_entry_fee"]

        # Optional attributes with defaults
        self.current_amount: Decimal = kwargs.get("current_amount", self.initial_amount)
        self.status: PositionStatus = kwargs.get("status", PositionStatus.OPEN)
        self.trailing_sl_price: Optional[Decimal] = kwargs.get("trailing_sl_price")
        self.time_of_tp1: Optional[datetime] = kwargs.get("time_of_tp1")
        self.last_checked_bar_ts: Optional[int] = kwargs.get("last_checked_bar_ts")

        # Performance tracking attributes
        self.mfe: Decimal = kwargs.get("mfe", Decimal("0"))
        self.mae: Decimal = kwargs.get("mae", Decimal("0"))
        self.peak_price_since_entry: Decimal = kwargs.get(
            "peak_price_since_entry", self.entry_price
        )
        self.peak_price_since_tp1: Optional[Decimal] = kwargs.get(
            "peak_price_since_tp1"
        )
        self.realized_profit: Decimal = kwargs.get("realized_profit", Decimal("0.0"))

        # State flags
        self.is_trailing_active: bool = kwargs.get("is_trailing_active", False)
        self.moved_to_breakeven: bool = kwargs.get("moved_to_breakeven", False)
        self.scaled_out_on_adverse: bool = kwargs.get("scaled_out_on_adverse", False)
        self.adverse_scaleout_timestamp: Optional[datetime] = kwargs.get(
            "adverse_scaleout_timestamp"
        )

        # MAE/MFE tracking
        self.mae_breach_counter: int = kwargs.get("mae_breach_counter", 0)

        # New attributes for refactoring features
        self.intrabar_breach_started_at: Optional[int] = kwargs.get(
            "intrabar_breach_started_at"
        )
        self.scaleout_suspend_until_bar_ts: Optional[int] = kwargs.get(
            "scaleout_suspend_until_bar_ts"
        )
        self.progress_breakeven_eligible: bool = kwargs.get(
            "progress_breakeven_eligible", False
        )
        self.defer_stale_exit_until_ts: Optional[int] = kwargs.get(
            "defer_stale_exit_until_ts"
        )

        # Enhanced R-multiple tracking
        self.peak_favorable_r: Decimal = kwargs.get("peak_favorable_r", Decimal("0"))
        self.peak_adverse_r: Decimal = kwargs.get("peak_adverse_r", Decimal("0"))
        self.current_r: Decimal = kwargs.get("current_r", Decimal("0"))

        # Position lifecycle tracking
        self.bars_held: int = kwargs.get("bars_held", 0)
        self.creation_timestamp: int = kwargs.get(
            "creation_timestamp", int(time.time() * 1000)
        )

        # Exit condition tracking
        self.exit_conditions_met: List[str] = kwargs.get("exit_conditions_met", [])
        self.last_exit_check_timestamp: Optional[int] = kwargs.get(
            "last_exit_check_timestamp"
        )

        # Enhanced trailing and breakeven state
        self.breakeven_level: Optional[Decimal] = kwargs.get("breakeven_level")
        self.trailing_start_r: Optional[Decimal] = kwargs.get("trailing_start_r")
        self.max_adverse_r_since_entry: Decimal = kwargs.get(
            "max_adverse_r_since_entry", Decimal("0")
        )
        self.max_adverse_r_since_tp1: Decimal = kwargs.get(
            "max_adverse_r_since_tp1", Decimal("0")
        )

        # PEAK-RESET PROTECTION: R-DECAY override state tracking for peak-update reset
        self.last_rdecay_peak: Optional[Decimal] = kwargs.get("last_rdecay_peak")
        self.rdecay_override_active: bool = kwargs.get("rdecay_override_active", False)

        # AGGRESSIVE PEAK EXIT + TP1 QUALITY RESET: Ratio tracking for quality assessment
        self.current_ratio: Decimal = kwargs.get(
            "current_ratio", Decimal("1.0")
        )  # Ratio from entry (MFE/MAE)

        # TP1 QUALITY RESET: Post-TP1 ratio tracking for adaptive trailing
        self.peak_favorable_r_beyond_tp1: Decimal = kwargs.get(
            "peak_favorable_r_beyond_tp1", Decimal("0")
        )  # Relative gain BEYOND TP1a
        self.max_adverse_r_since_tp1_post: Decimal = kwargs.get(
            "max_adverse_r_since_tp1_post", Decimal("0")
        )
        self.ratio_since_tp1: Decimal = kwargs.get("ratio_since_tp1", Decimal("0"))
        self.tp1_ratio_reset_timestamp: Optional[datetime] = kwargs.get(
            "tp1_ratio_reset_timestamp"
        )

        # Weak post-TP1 detection: tracks choppy post-TP1 moves that never develop into late bloomers
        self.weak_post_tp1_detected: bool = kwargs.get("weak_post_tp1_detected", False)
        self.weak_post_tp1_since: Optional[datetime] = kwargs.get("weak_post_tp1_since")
        self.consecutive_low_ratio_checks: int = kwargs.get(
            "consecutive_low_ratio_checks", 0
        )

        # POST-TP1 PROBATION: Universal tightening immediately after TP1
        self.post_tp1_probation_start: Optional[datetime] = kwargs.get(
            "post_tp1_probation_start"
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PositionState":
        """Creates a PositionState object from a dictionary, safely converting types."""
        processed_data = data.copy()
        for key, value in processed_data.items():
            if value is None:
                continue
            if key.endswith(
                (
                    "_price",
                    "_amount",
                    "_cost",
                    "_value",
                    "_atr",
                    "_mult",
                    "_pnl",
                    "_excursion",
                    "_r",
                )
            ) or key in [
                "mfe",
                "mae",
                "peak_price_since_entry",
                "total_entry_fee",
                "peak_price_since_tp1",
                "tp1a_price",
                "stall_min_r_advance",
                "trail_activation_r_multiple",
                "peak_favorable_r",
                "peak_adverse_r",
                "current_r",
                "max_adverse_r_since_entry",
                "max_adverse_r_since_tp1",
                "realized_profit",
                "last_rdecay_peak",
                "current_ratio",
                "peak_favorable_r_beyond_tp1",
                "max_adverse_r_since_tp1_post",
                "ratio_since_tp1",
            ]:
                processed_data[key] = _to_decimal(value, f"position.{key}")
            elif key.endswith("_time") or key in [
                "tp1_ratio_reset_timestamp",
                "weak_post_tp1_since",
                "post_tp1_probation_start",
            ]:
                processed_data[key] = (
                    datetime.fromisoformat(value) if isinstance(value, str) else value
                )
            elif key == "adverse_scaleout_timestamp":
                # Persisted positions store this as ISO8601; convert back for time arithmetic
                processed_data[key] = (
                    datetime.fromisoformat(value) if isinstance(value, str) else value
                )
        if "side" in processed_data and processed_data["side"] is not None:
            processed_data["side"] = TradeSide(processed_data["side"])
        if "status" in processed_data and processed_data["status"] is not None:
            processed_data["status"] = PositionStatus(processed_data["status"])
        return cls(**processed_data)

    def to_dict(self) -> Dict[str, Any]:
        data = self.__dict__.copy()
        for key, val in data.items():
            if isinstance(val, Decimal):
                data[key] = str(val)
            elif isinstance(val, datetime):
                data[key] = val.isoformat()
            elif isinstance(val, Enum):
                data[key] = val.value
        return data

    def create_checkpoint(self) -> Dict[str, Any]:
        """Create a checkpoint of current state for rollback purposes."""
        return self.to_dict()

    def restore_from_checkpoint(self, checkpoint: Dict[str, Any]) -> None:
        """Restore position state from a checkpoint."""
        # Restore all attributes from checkpoint
        for key, value in checkpoint.items():
            if hasattr(self, key):
                # Convert back to appropriate types
                if key.endswith(
                    (
                        "_price",
                        "_amount",
                        "_cost",
                        "_value",
                        "_atr",
                        "_mult",
                        "_pnl",
                        "_excursion",
                        "_r",
                    )
                ) or key in [
                    "mfe",
                    "mae",
                    "peak_price_since_entry",
                    "total_entry_fee",
                    "peak_price_since_tp1",
                    "tp1a_price",
                    "stall_min_r_advance",
                    "trail_activation_r_multiple",
                    "peak_favorable_r",
                    "peak_adverse_r",
                    "current_r",
                    "max_adverse_r_since_entry",
                    "max_adverse_r_since_tp1",
                    "realized_profit",
                    "last_rdecay_peak",
                    "current_ratio",
                    "peak_favorable_r_beyond_tp1",
                    "max_adverse_r_since_tp1_post",
                    "ratio_since_tp1",
                ]:
                    setattr(self, key, _to_decimal(value, f"checkpoint.{key}"))
                elif key.endswith("_time") or key in [
                    "tp1_ratio_reset_timestamp",
                    "weak_post_tp1_since",
                    "post_tp1_probation_start",
                ]:
                    setattr(
                        self,
                        key,
                        (
                            datetime.fromisoformat(value)
                            if isinstance(value, str)
                            else value
                        ),
                    )
                elif key == "side":
                    setattr(
                        self, key, TradeSide(value) if isinstance(value, str) else value
                    )
                elif key == "status":
                    setattr(
                        self,
                        key,
                        PositionStatus(value) if isinstance(value, str) else value,
                    )
                else:
                    setattr(self, key, value)

    def update_mfe_mae(self, current_price: Decimal):
        if self.side == TradeSide.BUY:
            favorable_excursion = current_price - self.entry_price
            if favorable_excursion > self.mfe:
                self.mfe = favorable_excursion
            adverse_excursion = self.entry_price - current_price
            if adverse_excursion > self.mae:
                self.mae = adverse_excursion
            if current_price > self.peak_price_since_entry:
                self.peak_price_since_entry = current_price
        else:  # SELL
            favorable_excursion = self.entry_price - current_price
            if favorable_excursion > self.mfe:
                self.mfe = favorable_excursion
            adverse_excursion = current_price - self.entry_price
            if adverse_excursion > self.mae:
                self.mae = adverse_excursion
            if current_price < self.peak_price_since_entry:
                self.peak_price_since_entry = current_price

        # Calculate current_ratio from MFE/MAE for quality assessment
        if self.mae < Decimal("0.001") * abs(self.entry_price):
            # Extremely clean - virtually no adverse movement (< 0.1% of entry price)
            # Cap at 50 instead of 999 to avoid incorrectly triggering extreme thresholds
            self.current_ratio = Decimal("50.0")
        elif self.mfe < Decimal("0.001") * abs(self.entry_price):
            # No meaningful favorable movement yet
            self.current_ratio = Decimal("1.0")
        else:
            # Normal calculation: MFE / MAE
            self.current_ratio = (
                self.mfe / self.mae if self.mae > 0 else Decimal("50.0")
            )

    def update_r_multiples(self, current_price: Decimal):
        """Enhanced R-multiple tracking for better position management."""
        if self.initial_risk_atr <= 0:
            return

        # Calculate current R-multiple
        if self.side == TradeSide.BUY:
            profit_per_unit = current_price - self.entry_price
        else:
            profit_per_unit = self.entry_price - current_price

        self.current_r = profit_per_unit / self.initial_risk_atr

        # Update peak favorable R
        if self.current_r > self.peak_favorable_r:
            self.peak_favorable_r = self.current_r

        # Update peak adverse R (absolute value)
        adverse_r = abs(min(self.current_r, Decimal("0")))
        # Defensive: ensure stored adverse trackers are Decimal
        if not isinstance(self.max_adverse_r_since_entry, Decimal):
            try:
                self.max_adverse_r_since_entry = Decimal(
                    str(self.max_adverse_r_since_entry)
                )
            except Exception:
                self.max_adverse_r_since_entry = Decimal("0")
        if not isinstance(self.max_adverse_r_since_tp1, Decimal):
            try:
                self.max_adverse_r_since_tp1 = Decimal(
                    str(self.max_adverse_r_since_tp1)
                )
            except Exception:
                self.max_adverse_r_since_tp1 = Decimal("0")
        if adverse_r > self.peak_adverse_r:
            self.peak_adverse_r = adverse_r

        # Track max adverse since entry
        if adverse_r > self.max_adverse_r_since_entry:
            self.max_adverse_r_since_entry = adverse_r

        # Track max adverse since TP1 (if applicable)
        if (
            self.status == PositionStatus.PARTIALLY_CLOSED
            and adverse_r > self.max_adverse_r_since_tp1
        ):
            self.max_adverse_r_since_tp1 = adverse_r

        # TP1 QUALITY RESET: Track post-TP1 R-multiples for adaptive ratio
        if self.tp1a_hit and self.tp1_ratio_reset_timestamp:
            # Calculate R from TP1 price (not entry price)
            tp1_price = self.tp1a_price

            # Defensive: tp1_price may be None when loading from state or
            # when TP1a was not properly recorded. Handle gracefully and
            # avoid raising TypeError. Log a warning to help debugging.
            if tp1_price is None:
                logger.warning(
                    "[%s] TP1 price is None; skipping post-TP1 R calculations",
                    getattr(self, "symbol_id", "<unknown>"),
                )
                favorable_from_tp1 = Decimal("0")
                adverse_from_tp1 = Decimal("0")
            else:
                if self.side == TradeSide.BUY:
                    try:
                        favorable_from_tp1 = max(
                            current_price - tp1_price, Decimal("0")
                        )
                        adverse_from_tp1 = max(tp1_price - current_price, Decimal("0"))
                    except Exception:
                        favorable_from_tp1 = Decimal("0")
                        adverse_from_tp1 = Decimal("0")
                        logger.exception(
                            "Error computing post-TP1 excursions for %s",
                            getattr(self, "symbol_id", "<unknown>"),
                        )
                else:  # SELL
                    try:
                        favorable_from_tp1 = max(
                            tp1_price - current_price, Decimal("0")
                        )
                        adverse_from_tp1 = max(current_price - tp1_price, Decimal("0"))
                    except Exception:
                        favorable_from_tp1 = Decimal("0")
                        adverse_from_tp1 = Decimal("0")
                        logger.exception(
                            "Error computing post-TP1 excursions for %s",
                            getattr(self, "symbol_id", "<unknown>"),
                        )

            # Convert to R-multiples
            favorable_r = favorable_from_tp1 / self.initial_risk_atr
            adverse_r_tp1 = adverse_from_tp1 / self.initial_risk_atr

            # Update peaks since TP1
            if favorable_r > self.peak_favorable_r_beyond_tp1:
                self.peak_favorable_r_beyond_tp1 = favorable_r

            if adverse_r_tp1 > self.max_adverse_r_since_tp1_post:
                self.max_adverse_r_since_tp1_post = adverse_r_tp1

            # Calculate ratio since TP1
            if self.max_adverse_r_since_tp1_post < Decimal("0.01"):
                # Extremely clean - virtually no adverse movement (less than 0.01R)
                # Cap at 50 instead of 999 to avoid triggering loose thresholds incorrectly
                self.ratio_since_tp1 = Decimal("50.0")
            elif self.peak_favorable_r_beyond_tp1 < Decimal("0.05"):
                # Too early to assess - stay neutral
                self.ratio_since_tp1 = Decimal("1.0")
            else:
                # Normal calculation: favorable / adverse
                self.ratio_since_tp1 = (
                    self.peak_favorable_r_beyond_tp1 / self.max_adverse_r_since_tp1_post
                )

    def calculate_mfe_mae_r_multiples(self) -> Tuple[Decimal, Decimal]:
        """Calculate MFE and MAE in R-multiple terms."""
        if self.initial_risk_atr <= 0:
            return Decimal("0"), Decimal("0")

        # MFE and MAE are already per-unit price differences
        # R-multiple = price_excursion / initial_risk_per_unit
        mfe_r = self.mfe / self.initial_risk_atr
        mae_r = self.mae / self.initial_risk_atr

        return mfe_r, mae_r

    def is_stale(self, max_minutes: int) -> bool:
        """Check if position has exceeded maximum hold time."""
        if max_minutes <= 0:
            return False
        age = datetime.now(timezone.utc) - self.entry_time
        return age >= timedelta(minutes=max_minutes)

    def get_bars_held(self, timeframe_seconds: float) -> int:
        """Calculate number of bars held based on timeframe."""
        if timeframe_seconds <= 0:
            return 0
        age_seconds = (datetime.now(timezone.utc) - self.entry_time).total_seconds()
        return int(age_seconds / timeframe_seconds)

    def should_apply_breakeven(self, trigger_r: Decimal) -> bool:
        """Check if position qualifies for breakeven stop adjustment."""
        return (
            not self.moved_to_breakeven
            and trigger_r > 0
            and self.current_r >= trigger_r
        )

    def calculate_breakeven_price(self, offset_atr_mult: Decimal) -> Decimal:
        """Calculate breakeven price with optional ATR offset."""
        offset = self.entry_atr * offset_atr_mult
        if self.side == TradeSide.BUY:
            return self.entry_price + offset
        else:
            return self.entry_price - offset

    def update_peak_price_since_tp1(self, current_price: Decimal):
        """Tracks the most favorable price excursion since TP1 was hit."""
        if self.peak_price_since_tp1 is None:
            return

        if self.side == TradeSide.BUY and current_price > self.peak_price_since_tp1:
            self.peak_price_since_tp1 = current_price
        elif self.side == TradeSide.SELL and current_price < self.peak_price_since_tp1:
            self.peak_price_since_tp1 = current_price

    def reset_ratio_tracking_at_tp1(self):
        """
        TP1 QUALITY RESET: Reset ratio tracking when TP1 is hit.

        From this point, we track:
        - Peak favorable R from TP1 price
        - Max adverse R from TP1 price
        - Calculate new ratio independent of pre-TP1 movement

        This allows us to recognize trades that start choppy but become clean after TP1.
        """
        self.peak_favorable_r_beyond_tp1 = Decimal("0")
        self.max_adverse_r_since_tp1_post = Decimal("0")
        self.ratio_since_tp1 = Decimal("1.0")  # Start neutral
        self.tp1_ratio_reset_timestamp = datetime.now(timezone.utc)

        if logger.isEnabledFor(logging.INFO):
            logger.info(
                f"[{self.symbol_id}] 5D: Ratio tracking reset at TP1. "
                f"Entry ratio: {self.current_ratio:.2f}, "
                f"now tracking post-TP1 quality independently."
            )

    def get_effective_ratio_for_trailing(self) -> Tuple[Decimal, str]:
        """
        TP1 QUALITY RESET: Get the appropriate ratio for trailing stop decisions.

        Returns:
            (ratio, source) where source is "post-TP1" or "from-entry"
        """
        # After TP1, use post-TP1 ratio if we have enough data
        if (
            self.tp1a_hit
            and self.tp1_ratio_reset_timestamp
            and self.peak_favorable_r_beyond_tp1 > Decimal("0.1")
        ):
            # We have meaningful post-TP1 movement
            return self.ratio_since_tp1, "post-TP1"

        # Before TP1 or insufficient post-TP1 data
        return self.current_ratio, "from-entry"

    def get_quality_adjusted_multiplier(self, base_mult: Decimal) -> Decimal:
        """
        QUALITY-WEIGHTED TRAILING: Adjust trail distance based on trade cleanliness (MFE/MAE ratio).

        Logic:
        - High MFE/MAE ratio (>5) = clean trend → tighten trail by 25%
        - Low MFE/MAE ratio (<2) = choppy trade → widen trail by 15%
        - Medium ratio (2-5) = normal → keep base multiplier

        Returns:
            Adjusted multiplier (0.75x to 1.15x of base)
        """
        # TP1 QUALITY RESET: Use effective ratio (post-TP1 if available, otherwise from-entry)
        ratio, source = self.get_effective_ratio_for_trailing()

        # Need minimum movement to assess quality (only for from-entry ratio)
        if source == "from-entry":
            mfe_r, mae_r = self.calculate_mfe_mae_r_multiples()
            if mfe_r < Decimal("0.3"):
                return base_mult  # Too early to judge, use base

        # Apply adjustment based on ratio
        # REVISED THRESHOLDS: More aggressive tightening for clean trends
        # Oct 16 enhancement: Added very clean trend tier for better profit capture
        if ratio > Decimal("6.0"):
            # Extremely clean trend: minimal adverse movement
            adjustment = Decimal("0.55")  # Tighten by 45%
            logger.debug(
                f"[{self.symbol_id}] Extremely clean trend detected ({source} ratio={ratio:.2f}), tightening trail to {adjustment:.2f}x"
            )
        elif ratio > Decimal("3.0"):
            # Clean trend: price moving smoothly in our direction
            adjustment = Decimal("0.65")  # Tighten by 35% (was 25%)
            logger.debug(
                f"[{self.symbol_id}] Clean trend detected ({source} ratio={ratio:.2f}), tightening trail to {adjustment:.2f}x"
            )
        elif ratio < Decimal("1.5"):
            # Choppy trade: significant adverse movement
            adjustment = Decimal("1.15")  # Widen by 15%
            logger.debug(
                f"[{self.symbol_id}] Choppy trade detected ({source} ratio={ratio:.2f}), widening trail to {adjustment:.2f}x"
            )
        else:
            # Normal trade quality (1.5-3.0 ratio)
            adjustment = Decimal("1.0")

        return base_mult * adjustment

    def get_stage_adjusted_multiplier(self, base_mult: Decimal) -> Decimal:
        """
        STAGE-WEIGHTED TRAILING: Adjust trail distance based on R-multiple achieved (trade lifecycle stage).

        Logic:
        - Stage 1 (0-0.5R):   Give room for initial move → 1.2x base
        - Stage 2 (0.5-1.5R): Normal protection → 1.0x base
        - Stage 3 (1.5-2.5R): Start locking in → 0.85x base
        - Stage 4 (2.5-4R):   Aggressive protection → 0.75x base
        - Stage 5 (>4R):      Maximum protection → 0.65x base

        Returns:
            Adjusted multiplier (0.65x to 1.2x of base)
        """
        current_r = self.current_r

        if current_r < Decimal("0.5"):
            # Very early stage: Give room for initial move
            adjustment = Decimal("1.2")
            stage = "Early (0-0.5R)"
        elif current_r < Decimal("1.5"):
            # Developing stage: Start protecting profit (tightened from 1.0 to 0.90)
            adjustment = Decimal("0.90")
            stage = "Dev (0.5-1.5R)"
        elif current_r < Decimal("2.5"):
            # Profitable stage: Start tightening
            adjustment = Decimal("0.85")
            stage = "Prof (1.5-2.5R)"
        elif current_r < Decimal("4.0"):
            # Win stage: Aggressive protection
            adjustment = Decimal("0.75")
            stage = "Win (2.5-4R)"
        else:
            # Big win stage: Maximum protection
            adjustment = Decimal("0.65")
            stage = "Big Win (>4R)"

        logger.debug(
            f"[{self.symbol_id}] Stage {stage} at {current_r:.2f}R, adjustment: {adjustment:.2f}x"
        )

        return base_mult * adjustment

    def update_trailing_stop(
        self, strategy: "StrategyConfig", current_atr: Decimal, current_price: Decimal
    ):
        """
        Updates the trailing stop loss price with intelligent adaptation.
        Uses current ATR and adapts based on trade quality (Phase 1) and stage (Phase 2).
        """
        if not self.is_trailing_active:
            return

        # Use current ATR for a more responsive trail, fallback to entry ATR if needed
        trail_atr = current_atr if current_atr and current_atr > 0 else self.entry_atr

        # QUALITY-WEIGHTED + STAGE-WEIGHTED TRAILING: Apply intelligent adjustments
        quality_mult = self.get_quality_adjusted_multiplier(strategy.trail_sl_atr_mult)
        stage_mult = self.get_stage_adjusted_multiplier(strategy.trail_sl_atr_mult)

        # Calculate MFE/MAE ratio for dynamic weighting
        # Use get_effective_ratio_for_trailing() to get post-TP1 ratio if available
        ratio, ratio_source = self.get_effective_ratio_for_trailing()

        # AGGRESSIVE PEAK EXIT: Store ratio for clean trade fast exit check
        self.current_ratio = ratio

        # REMOVED: Old WEAK POST-TP1 DETECTION (30-minute counter-based)
        # This legacy detection has been replaced by Quality-Based Probation (2-minute time-based)
        # See POST-TP1 PROBATION section below for the new implementation

        # Combine with weighted average (favor stage for large winners, quality for early trades)
        # QUALITY-WEIGHTED ENHANCEMENT: Boost quality weight to 85% for extremely clean trends (ratio > 5.0)
        if ratio > Decimal("5.0"):
            # Extremely clean trend: Quality dominates (85% quality, 15% stage)
            weight_q, weight_s = Decimal("0.85"), Decimal("0.15")
            weighting_scheme = "85/15"
        elif self.current_r < Decimal("1.5"):
            # Early trade: Quality matters more (70% quality, 30% stage)
            weight_q, weight_s = Decimal("0.70"), Decimal("0.30")
            weighting_scheme = "70/30"
        else:
            # Mid-trade: Balance quality and stage (60% quality, 40% stage)
            weight_q, weight_s = Decimal("0.60"), Decimal("0.40")
            weighting_scheme = "60/40"

        final_mult = quality_mult * weight_q + stage_mult * weight_s

        # ═══════════════════════════════════════════════════════════════════════════
        # POST-TP1 PROBATION (Quality-Based Protection - Option C Enhanced)
        # ═══════════════════════════════════════════════════════════════════════════
        # After TP1, apply ADAPTIVE protection based on trade quality
        # This prevents rapid giveback on choppy trades while letting clean trades run
        #
        # OPTION C: Quality-Based Probation (0-2 min):
        # - Choppy trades (ratio <3.0):    0.30x mult (TIGHT - 70% tighter, allows breathing room)
        # - Medium trades (ratio 3.0-5.0): 0.35x mult (MODERATE - 65% tighter)
        # - Clean trades (ratio >5.0):     0.40x mult (LOOSE - 60% tighter, let winners run)
        #
        # WEAK DETECTION (2+ min):
        # - If ratio still <3.0: 0.40x mult (VERY TIGHT - 60% tighter, emergency exit)
        # ═══════════════════════════════════════════════════════════════════════════

        if self.tp1a_hit and self.post_tp1_probation_start:
            time_since_tp1_min = (
                datetime.now(timezone.utc) - self.post_tp1_probation_start
            ).total_seconds() / 60

            if time_since_tp1_min < 2:  # CHANGED: 10min → 2min probation
                # STAGE 1: First 2 minutes - QUALITY-BASED probation
                if ratio < Decimal("3.0"):
                    # CHOPPY: Very tight protection (70% tighter than base)
                    # Prevents excessive giveback on choppy trades
                    probation_mult = Decimal("0.30")  # CHANGED: 0.60 → 0.30
                    quality_tier = "CHOPPY"
                elif ratio < Decimal("5.0"):
                    # MEDIUM: Tight protection (65% tighter than base)
                    probation_mult = Decimal("0.35")  # CHANGED: 0.70 → 0.35
                    quality_tier = "MEDIUM"
                else:
                    # CLEAN: Moderate protection (60% tighter than base)
                    probation_mult = Decimal("0.40")  # CHANGED: 0.85 → 0.40
                    quality_tier = "CLEAN"

                final_mult = strategy.trail_sl_atr_mult * probation_mult
                weighting_scheme = f"QUALITY-PROBATION-{quality_tier}"
                if logger.isEnabledFor(logging.INFO):
                    logger.info(
                        f"[{self.symbol_id}] Quality-based probation at {self.current_r:.2f}R: "
                        f"{time_since_tp1_min:.1f} min since TP1, ratio {ratio:.2f} ({quality_tier}) "
                        f"→ {probation_mult}x mult ({ratio_source})"
                    )
            elif ratio < Decimal("3.0"):
                # STAGE 2: After 2 min with low ratio - WEAK trade detection
                # Very tight trail (60% tighter) to lock in remaining gains
                if not self.weak_post_tp1_detected:
                    self.weak_post_tp1_detected = True
                    self.weak_post_tp1_since = datetime.now(timezone.utc)
                    logger.warning(
                        f"[{self.symbol_id}] WEAK POST-TP1 DETECTED (Quality Probation): "
                        f"Ratio {ratio:.2f} < 3.0 after {time_since_tp1_min:.1f} min post-TP1. "
                        f"Tightening trail to lock in gains (0.40x)."
                    )

                final_mult = strategy.trail_sl_atr_mult * Decimal("0.40")
                weighting_scheme = "WEAK-POST-TP1"
                if logger.isEnabledFor(logging.INFO):
                    logger.info(
                        f"[{self.symbol_id}] Weak post-TP1 tight trail at {self.current_r:.2f}R: "
                        f"ratio {ratio:.2f} stayed low for {(datetime.now(timezone.utc) - self.weak_post_tp1_since).total_seconds()/60:.1f} min → 0.40x mult"
                    )
            # else: ratio ≥3.0 after 2 min - quality improved, let normal trailing resume
        else:
            # NOT in Post-TP1 Probation - use normal Quality + Stage weighting
            # Combine with weighted average (favor stage for large winners, quality for early trades)
            if ratio > Decimal("5.0"):
                # Extremely clean trend: Quality dominates (85% quality, 15% stage)
                weight_q, weight_s = Decimal("0.85"), Decimal("0.15")
                weighting_scheme = "85/15"
            elif self.current_r < Decimal("1.5"):
                # Early trade: Quality matters more (70% quality, 30% stage)
                weight_q, weight_s = Decimal("0.70"), Decimal("0.30")
                weighting_scheme = "70/30"
            else:
                # Mid-trade: Balance quality and stage (60% quality, 40% stage)
                weight_q, weight_s = Decimal("0.60"), Decimal("0.40")
                weighting_scheme = "60/40"

            final_mult = quality_mult * weight_q + stage_mult * weight_s

        # RATIO-DECAY OVERRIDE - R-multiple based decay detection
        # On clean trends, we track R-multiple decay from peak, not price percentage
        # This is more sensitive to momentum changes on strong moves

        # Calculate both price decay % and R-multiple decay
        price_decay_pct = Decimal("0")
        if self.peak_price_since_entry > Decimal(
            "0"
        ):  # Safety check to prevent division by zero
            if self.side == TradeSide.BUY:
                price_decay_pct = (
                    (self.peak_price_since_entry - current_price)
                    / self.peak_price_since_entry
                    * Decimal("100")
                )
                price_decay_pct = max(price_decay_pct, Decimal("0"))
            else:  # SELL
                price_decay_pct = (
                    (current_price - self.peak_price_since_entry)
                    / self.peak_price_since_entry
                    * Decimal("100")
                )
                price_decay_pct = max(price_decay_pct, Decimal("0"))

        # R-multiple decay: how much we've dropped from peak R-multiple
        r_decay_pct = Decimal("0")
        if self.peak_favorable_r > Decimal(
            "0.1"
        ):  # Only calculate if we've had meaningful profit
            r_decay_pct = (
                (self.peak_favorable_r - self.current_r)
                / self.peak_favorable_r
                * Decimal("100")
            )
            r_decay_pct = max(r_decay_pct, Decimal("0"))  # Ensure non-negative

        # Use R-decay for clean trends (more sensitive), price decay for choppy trades
        decay_metric = r_decay_pct if ratio > Decimal("3.0") else price_decay_pct

        # PEAK-RESET PROTECTION
        # If price makes new peak after R-DECAY triggered, reset to normal trailing
        # This prevents "ratchet effect" where stops only get tighter, never looser
        if self.rdecay_override_active and self.last_rdecay_peak is not None:
            # Check if we have a meaningful new peak (+5% or more)
            peak_increase_pct = Decimal("0")
            if self.last_rdecay_peak > Decimal("0"):
                peak_increase_pct = (
                    (self.peak_favorable_r - self.last_rdecay_peak)
                    / self.last_rdecay_peak
                    * Decimal("100")
                )

            # Reset conditions:
            # 1. New peak is +5% higher than peak when R-DECAY triggered
            # 2. Current R is >90% of new peak (price near highs, not just a spike)
            # 3. R-DECAY override is currently active
            if peak_increase_pct > Decimal("5.0") and self.current_r > (
                self.peak_favorable_r * Decimal("0.90")
            ):
                # Clear R-DECAY override state
                self.rdecay_override_active = False
                self.last_rdecay_peak = None
                if logger.isEnabledFor(logging.INFO):
                    logger.info(
                        f"[{self.symbol_id}] Peak-Reset Protection: new peak {self.peak_favorable_r:.3f}R "
                        f"(+{peak_increase_pct:.1f}%), current {self.current_r:.3f}R. "
                        f"R-DECAY override cleared, returning to normal trailing."
                    )
                # Allow normal Quality-Weighted logic to continue (no early return)

        # RATIO-DECAY OVERRIDE LOGIC
        # CRITICAL: Check peak_favorable_r, not current_r!
        # We want to detect decay FROM a profitable peak, not require current profit
        # Use lower threshold for post-TP1 positions (they start from lower base after scale-out)
        decay_threshold = (
            Decimal("0.15")
            if self.status == PositionStatus.PARTIALLY_CLOSED
            else Decimal("0.30")
        )

        # SAFEGUARD: Don't let R-DECAY override make probation TIGHTER for choppy trades
        # During Post-TP1 Probation on choppy trades, we already have tight protection (0.60x)
        # R-DECAY should only activate if it's MEANINGFULLY tighter (not just 0.40x vs 0.60x)
        skip_rdecay_during_choppy_probation = (
            self.tp1a_hit
            and self.post_tp1_probation_start
            and (
                datetime.now(timezone.utc) - self.post_tp1_probation_start
            ).total_seconds()
            / 60
            < 10
            and ratio < Decimal("3.0")  # Choppy trade already protected at 0.60x
        )

        if (
            self.peak_favorable_r > decay_threshold
            and not skip_rdecay_during_choppy_probation
        ):  # If we reached meaningful profit
            # RATIO-DECAY (revised): Lower ratio thresholds to protect more realistic trades
            # Old: ratio >3.0 minimum was too strict (only protects MFE/MAE >3.0)
            # New: ratio >1.5 minimum covers normal crypto volatility (MAE ~40% of MFE)
            if ratio > Decimal("10.0"):  # Extremely clean: very sensitive to R-decay
                if decay_metric > Decimal("15.0"):  # Dropped >15% from peak R
                    final_mult = strategy.trail_sl_atr_mult * Decimal("0.25")
                    weighting_scheme = f"R-DECAY-{decay_metric:.0f}%"
                    # Peak-Reset Protection: Track R-DECAY override state
                    self.rdecay_override_active = True
                    self.last_rdecay_peak = self.peak_favorable_r
                    logger.warning(
                        f"[{self.symbol_id}] R-DECAY (Extreme): {decay_metric:.1f}% R-drop from {self.peak_favorable_r:.3f}R, ratio {ratio:.2f} → 0.25x"
                    )
                elif decay_metric > Decimal("10.0"):  # Dropped >10% from peak R
                    final_mult = strategy.trail_sl_atr_mult * Decimal("0.35")
                    weighting_scheme = f"R-DECAY-{decay_metric:.0f}%"
                    # Peak-Reset Protection: Track R-DECAY override state
                    self.rdecay_override_active = True
                    self.last_rdecay_peak = self.peak_favorable_r
                    if logger.isEnabledFor(logging.INFO):
                        logger.info(
                            f"[{self.symbol_id}] R-decay (extreme): {decay_metric:.1f}% R-drop from {self.peak_favorable_r:.3f}R, ratio {ratio:.2f} → 0.35x"
                        )
            elif ratio > Decimal("5.0"):  # Very clean: moderate R-decay sensitivity
                if decay_metric > Decimal("20.0"):  # Dropped >20% from peak R
                    final_mult = strategy.trail_sl_atr_mult * Decimal("0.30")
                    weighting_scheme = f"R-DECAY-{decay_metric:.0f}%"
                    # Peak-Reset Protection: Track R-DECAY override state
                    self.rdecay_override_active = True
                    self.last_rdecay_peak = self.peak_favorable_r
                    logger.warning(
                        f"[{self.symbol_id}] R-DECAY (Very Clean): {decay_metric:.1f}% R-drop from {self.peak_favorable_r:.3f}R, ratio {ratio:.2f} → 0.30x"
                    )
                elif decay_metric > Decimal("12.0"):  # Dropped >12% from peak R
                    final_mult = strategy.trail_sl_atr_mult * Decimal("0.40")
                    weighting_scheme = f"R-DECAY-{decay_metric:.0f}%"
                    # Peak-Reset Protection: Track R-DECAY override state
                    self.rdecay_override_active = True
                    self.last_rdecay_peak = self.peak_favorable_r
                    if logger.isEnabledFor(logging.INFO):
                        logger.info(
                            f"[{self.symbol_id}] R-decay (very clean): {decay_metric:.1f}% R-drop from {self.peak_favorable_r:.3f}R, ratio {ratio:.2f} → 0.40x"
                        )
            elif ratio > Decimal("2.5"):  # Clean: use price decay (less sensitive)
                if decay_metric > Decimal("25.0"):  # Price drop >25%
                    final_mult = strategy.trail_sl_atr_mult * Decimal("0.35")
                    weighting_scheme = f"P-DECAY-{decay_metric:.0f}%"
                    # Peak-Reset Protection: Track R-DECAY override state
                    self.rdecay_override_active = True
                    self.last_rdecay_peak = self.peak_favorable_r
                    logger.warning(
                        f"[{self.symbol_id}] Price DECAY (Clean): {decay_metric:.1f}% price drop, ratio {ratio:.2f} → 0.35x"
                    )
                elif decay_metric > Decimal("18.0"):  # Price drop >18%
                    final_mult = strategy.trail_sl_atr_mult * Decimal("0.45")
                    weighting_scheme = f"P-DECAY-{decay_metric:.0f}%"
                    # Peak-Reset Protection: Track R-DECAY override state
                    self.rdecay_override_active = True
                    self.last_rdecay_peak = self.peak_favorable_r
                    if logger.isEnabledFor(logging.INFO):
                        logger.info(
                            f"[{self.symbol_id}] Price decay (clean): {decay_metric:.1f}% price drop, ratio {ratio:.2f} → 0.45x"
                        )
            elif ratio > Decimal(
                "1.5"
            ):  # Moderate/choppy: higher thresholds, use R-decay
                # NEW TIER: Covers normal crypto volatility (MAE ~40% of MFE)
                # Use R-decay for better accuracy on choppy moves
                if r_decay_pct > Decimal("50.0"):  # Gave back >50% of R gains
                    final_mult = strategy.trail_sl_atr_mult * Decimal("0.40")
                    weighting_scheme = f"R-DECAY-{r_decay_pct:.0f}%"
                    # Peak-Reset Protection: Track R-DECAY override state
                    self.rdecay_override_active = True
                    self.last_rdecay_peak = self.peak_favorable_r
                    logger.warning(
                        f"[{self.symbol_id}] R-DECAY (Moderate): {r_decay_pct:.1f}% R-drop from {self.peak_favorable_r:.3f}R, ratio {ratio:.2f} → 0.40x"
                    )
                elif r_decay_pct > Decimal("35.0"):  # Gave back >35% of R gains
                    final_mult = strategy.trail_sl_atr_mult * Decimal("0.50")
                    weighting_scheme = f"R-DECAY-{r_decay_pct:.0f}%"
                    # Peak-Reset Protection: Track R-DECAY override state
                    self.rdecay_override_active = True
                    self.last_rdecay_peak = self.peak_favorable_r
                    if logger.isEnabledFor(logging.INFO):
                        logger.info(
                            f"[{self.symbol_id}] R-decay (moderate): {r_decay_pct:.1f}% R-drop from {self.peak_favorable_r:.3f}R, ratio {ratio:.2f} → 0.50x"
                        )

        # NOTE: WEAK POST-TP1 override logic is now integrated into POST-TP1 PROBATION above
        # It provides universal post-TP1 protection with progressive tightening

        trail_distance = trail_atr * final_mult

        # Log intelligent adjustments when they differ from base
        base_distance = trail_atr * strategy.trail_sl_atr_mult
        if abs(final_mult - strategy.trail_sl_atr_mult) > Decimal("0.01"):
            if logger.isEnabledFor(logging.INFO):
                logger.info(
                    f"[{self.symbol_id}] Intelligent trail at {self.current_r:.2f}R: "
                    f"distance={trail_distance:.6f} (base: {base_distance:.6f}) | "
                    f"mult: {final_mult:.3f}x (Q:{quality_mult:.3f} S:{stage_mult:.3f} "
                    f"W:{weighting_scheme} ratio:{ratio:.2f})"
                )

        if self.side == TradeSide.BUY:
            # Trail below the peak price since entry
            new_trailing_sl = self.peak_price_since_entry - trail_distance
            if (
                self.trailing_sl_price is None
                or new_trailing_sl > self.trailing_sl_price
            ):
                self.trailing_sl_price = new_trailing_sl
        else:  # SELL
            # Trail above the peak price since entry
            new_trailing_sl = self.peak_price_since_entry + trail_distance
            if (
                self.trailing_sl_price is None
                or new_trailing_sl < self.trailing_sl_price
            ):
                self.trailing_sl_price = new_trailing_sl


# Helper: _to_decimal stand-in (assuming it converts safely and logs warnings on failures)
def _to_decimal(value, name: str, default=Decimal("0")) -> Decimal:
    # Fast-path if already a Decimal
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        logger.warning(
            f"Invalid decimal value for {name}: {value}, defaulting to {default}"
        )
        return default
