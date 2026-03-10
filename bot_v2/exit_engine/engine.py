"""
Exit Condition Engine

Centralized exit evaluation with priority-based condition checking.
Extracted from bot.py (lines 1159-1550) - ~400 lines of exit logic.

Priority Order:
1. Catastrophic & Hard Stops (immediate protection)
2. Soft SL / Breakeven (safety first)
3. Trailing Stop (profit protection)
4. Take Profit (TP1a, TP1b)
5. Adverse Scale-out (risk reduction)
6. Stale Trade (time-based safety)
7. Advanced exits (MAE/MFE, bar-close conditions)
"""

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from bot_v2.models.enums import PositionSide, PositionStatus
from bot_v2.models.exit_condition import ExitCondition
from bot_v2.models.position import Position
from bot_v2.models.strategy_config import StrategyConfig

logger = logging.getLogger(__name__)


class ExitConditionEngine:
    """
    Evaluates all exit conditions in priority order.

    This engine centralizes exit logic that was previously scattered
    throughout bot.py. Each condition is checked in a specific order
    with clear precedence rules.

    Design:
    - Stateless: Each evaluation creates a new engine instance
    - Priority-based: Higher priority exits checked first
    - Defensive: Safety exits (stops) before profit exits
    - Testable: Each condition is a separate method
    """

    DECIMAL_ZERO = Decimal("0")
    DECIMAL_ONE_E_MINUS_8 = Decimal("1e-8")
    DECIMAL_POINT_ONE = Decimal("0.1")

    def __init__(
        self,
        position: Position,
        strategy: StrategyConfig,
        current_price: Decimal,
        current_atr: Decimal,
        current_bar_ts: Optional[int] = None,
    ):
        """
        Initialize exit engine for a single evaluation.

        Args:
            position: Current position state
            strategy: Strategy configuration
            current_price: Current market price
            current_atr: Current ATR value
            current_bar_ts: Current bar timestamp (milliseconds), None = use current time
        """
        self.pos = position
        self.strategy = strategy
        self.current_price = current_price
        self.current_atr = current_atr
        self.current_bar_ts = (
            current_bar_ts if current_bar_ts is not None else int(time.time() * 1000)
        )

        # Calculate derived values once
        self.side_buy = self.pos.side == PositionSide.LONG
        self.current_amount = self.pos.current_amount or self.pos.initial_amount

        # Calculate MFE/MAE R-multiples
        self.mfe_r = (
            self.pos.mfe / self.pos.initial_risk_atr
            if self.pos.initial_risk_atr > Decimal("0")
            else Decimal("0")
        )
        self.mae_r = (
            self.pos.mae / self.pos.initial_risk_atr
            if self.pos.initial_risk_atr > Decimal("0")
            else Decimal("0")
        )

    def evaluate_all_exits(self) -> Optional[ExitCondition]:
        """
        Evaluate all exit conditions in priority order.

        Returns first triggered exit or None if no exit conditions met.
        Priority order ensures safety exits (stops) checked before profit exits.
        """
        # Priority 1: Catastrophic & Hard Stops (immediate protection)
        exit_cond = self._check_catastrophic_stop()
        if exit_cond:
            return exit_cond

        exit_cond = self._check_hard_stop()
        if exit_cond:
            return exit_cond

        # Priority 2: Soft SL / Breakeven (safety before profit)
        exit_cond = self._check_soft_sl_continuous()
        if exit_cond:
            return exit_cond

        # Priority 3: Trailing Stop & Aggressive Peak Exit (profit protection)
        exit_cond = self._check_trailing_stop()
        if exit_cond:
            return exit_cond

        # Minimum hold time check (only blocks profit exits, not safety)
        if not self._is_minimum_hold_time_met():
            return None

        # Priority 4: Take Profit Targets (profit booking)
        if self.pos.status == PositionStatus.OPEN and self.strategy.tp1_enabled:
            exit_cond = self._check_tp1()
            if exit_cond:
                return exit_cond

        # Priority 5: Adverse Scale-out (risk reduction)
        exit_cond = self._check_adverse_scaleout()
        if exit_cond:
            return exit_cond

        # Priority 6: Stale Trade (time-based safety)
        exit_cond = self._check_stale_trade()
        if exit_cond:
            return exit_cond

        # Priority 7: Advanced exits (MAE/MFE, bar-close conditions)
        if not self._is_mae_suspended():
            exit_cond = self._check_intrabar_mae_mfe()
            if exit_cond:
                return exit_cond

        exit_cond = self._check_bar_close_conditions()
        if exit_cond:
            return exit_cond

        return None

    # ==================== Priority 1: Catastrophic & Hard Stops ====================

    def _check_catastrophic_stop(self) -> Optional[ExitCondition]:
        """
        Check if catastrophic stop triggered (6x ATR or configured mult).
        This is the absolute worst-case protection.
        """
        cat_mult = self.strategy.catastrophic_stop_mult
        if cat_mult <= Decimal("0"):
            return None

        cat_stop_distance = self.pos.initial_risk_atr * cat_mult

        if self.side_buy:
            cat_stop_price = self.pos.entry_price - cat_stop_distance
            triggered = self.current_price <= cat_stop_price
        else:
            cat_stop_price = self.pos.entry_price + cat_stop_distance
            triggered = self.current_price >= cat_stop_price

        if triggered:
            return ExitCondition(
                "CatastrophicStop",
                1,
                self.current_amount,
                self.current_price,
                f"Catastrophic stop at {self.current_price:.4f}",
            )
        return None

    def _check_hard_stop(self) -> Optional[ExitCondition]:
        """
        Check if hard stop loss triggered (5.5x ATR or configured mult).
        This is the primary stop loss protection.
        """
        if self.side_buy:
            triggered = self.current_price <= self.pos.hard_sl_price
        else:
            triggered = self.current_price >= self.pos.hard_sl_price

        if triggered:
            return ExitCondition(
                "HardSL",
                2,
                self.current_amount,
                self.current_price,
                f"Hard stop at {self.current_price:.4f}",
            )
        return None

    # ==================== Priority 2: Soft SL / Breakeven ====================

    def _check_soft_sl_continuous(self) -> Optional[ExitCondition]:
        """
        Check soft stop loss (4.5x ATR or breakeven level).

        SAFETY FIRST: Works immediately after 1-minute buffer to prevent whipsaws.
        Not dependent on bar timing - continuous protection.
        """
        if self.pos.soft_sl_price is None:
            return None
        if self.pos.status == PositionStatus.PARTIALLY_CLOSED:
            return None

        # Only 1 minute buffer to prevent immediate whipsaws
        current_time = datetime.now(timezone.utc)
        time_since_entry = current_time - self.pos.entry_time
        if time_since_entry.total_seconds() < 60:
            return None

        if self.side_buy:
            soft_sl_hit = self.current_price <= self.pos.soft_sl_price
        else:
            soft_sl_hit = self.current_price >= self.pos.soft_sl_price

        if soft_sl_hit:
            reason = "BreakevenStop" if self.pos.moved_to_breakeven else "SoftSL"
            return ExitCondition(
                reason,
                2,
                self.current_amount,
                self.current_price,
                f"{reason} hit at {self.current_price:.4f}",
            )
        return None

    # ==================== Priority 3: Trailing Stop ====================

    def _check_trailing_stop(self) -> Optional[ExitCondition]:
        """
        Check trailing stop and aggressive peak exit.

        Aggressive Peak Exit: For clean trades (configurable ratio/R), exit on configurable pullback from peak.
        ONLY ACTIVE AFTER TP1a - allows trades to reach 0.7R minimum before aggressive exits.

        Trailing Stop: Normal ATR-based trailing for all other cases.
        """
        # Get effective ratio (post-TP1 if available, otherwise entry)
        ratio = self._get_effective_ratio_for_trailing()

        # Aggressive peak exit for clean trades (ONLY AFTER TP1a)
        ape_min_ratio = self.strategy.ape_min_ratio
        ape_min_r = self.strategy.ape_min_r
        ape_pullback_pct = self.strategy.ape_pullback_pct

        if (
            ratio > ape_min_ratio
            and self.pos.peak_favorable_r > ape_min_r
            and self.pos.tp1a_hit
        ):  # Must hit TP1a first (~0.7R)

            quality_tier = "Extreme" if ratio > (ape_min_ratio * 2) else "VeryClean"

            if self.pos.peak_favorable_r > Decimal("0"):
                pullback_pct = (
                    self.pos.peak_favorable_r - self.pos.current_r
                ) / self.pos.peak_favorable_r

                if pullback_pct >= ape_pullback_pct:
                    logger.info(
                        f"[{self.pos.symbol_id}] AGGRESSIVE PEAK EXIT ({quality_tier}): "
                        f"Peak {self.pos.peak_favorable_r:.3f}R → Current {self.pos.current_r:.3f}R "
                        f"(-{pullback_pct*100:.1f}% >= -{ape_pullback_pct*100:.1f}% threshold) Ratio:{ratio:.1f}"
                    )
                    return ExitCondition(
                        "AggressivePeakExit",
                        6,
                        self.current_amount,
                        self.current_price,
                        f"Aggressive peak exit at {self.current_price:.4f}",
                    )

        # Normal trailing stop check
        if not self.pos.is_trailing_active or self.pos.trailing_sl_price is None:
            return None

        if self.side_buy:
            triggered = self.current_price <= self.pos.trailing_sl_price
        else:
            triggered = self.current_price >= self.pos.trailing_sl_price

        if triggered:
            return ExitCondition(
                "TrailExit",
                6,
                self.current_amount,
                self.current_price,
                f"Trailing stop at {self.current_price:.4f}",
            )
        return None

    # ==================== Priority 4: Take Profit ====================

    def _check_tp1(self) -> Optional[ExitCondition]:
        """
        Check TP1 targets (TP1a quick scalp, TP1b main target).

        TP1a: Quick scalp at 0.7x ATR (30% close)
        TP1b: Main target at 1.2x ATR (remaining position)
        """
        # TP1a: Quick scalp (if not hit yet)
        if not self.pos.tp1a_hit and self.pos.tp1a_price is not None:
            if self.side_buy:
                tp1a_triggered = self.current_price >= self.pos.tp1a_price
            else:
                tp1a_triggered = self.current_price <= self.pos.tp1a_price

            if tp1a_triggered:
                close_amount = (
                    self.current_amount
                    * self.strategy.tp1a_close_percent
                    / Decimal("100")
                ).quantize(self.DECIMAL_ONE_E_MINUS_8)
                return ExitCondition(
                    "TP1a",
                    4,
                    close_amount,
                    self.current_price,
                    f"TP1a scalp hit at {self.current_price:.4f} ({self.strategy.tp1a_close_percent:.0f}%)",
                )

        # TP1b: Main target (after TP1a hit)
        if (
            self.strategy.tp1_enabled
            and self.pos.tp1a_hit
            and self.current_amount > Decimal("0")
        ):
            if self.side_buy:
                tp1b_triggered = self.current_price >= self.pos.tp1_price
            else:
                tp1b_triggered = self.current_price <= self.pos.tp1_price

            if tp1b_triggered:
                return ExitCondition(
                    "TP1b",
                    4,
                    self.current_amount,
                    self.current_price,
                    f"TP1b target hit at {self.current_price:.4f} (remaining position)",
                )

        return None

    # ==================== Priority 5: Adverse Scale-out ====================

    def _check_adverse_scaleout(self) -> Optional[ExitCondition]:
        """
        Check if adverse scale-out should trigger.

        Reduces position by 50% when MAE reaches 2.5R (configurable).
        Only triggers once per position.
        """
        if (
            self.pos.status != PositionStatus.OPEN
            or self.strategy.partial_exit_on_adverse_r <= Decimal("0")
            or self.pos.scaled_out_on_adverse
        ):
            return None

        threshold_r = self.strategy.partial_exit_on_adverse_r

        if self.mae_r >= threshold_r:
            logger.info(
                f"[{self.pos.symbol_id}] Adverse scale-out triggered: "
                f"MAE {self.mae_r:.3f}R >= threshold {threshold_r:.3f}R"
            )
            close_amount = (
                self.current_amount * self.strategy.partial_exit_pct / Decimal("100")
            ).quantize(self.DECIMAL_ONE_E_MINUS_8)
            return ExitCondition(
                "AdverseScaleOut",
                5,
                close_amount,
                self.current_price,
                f"Adverse scale-out at {self.mae_r:.2f}R drawdown",
            )

        return None

    # ==================== Priority 6: Stale Trade ====================

    def _check_stale_trade(self) -> Optional[ExitCondition]:
        """
        Check if trade is stale (held too long with insufficient progress).

        Exits after configured time (default 600 minutes) if:
        - MFE < 0.3R (insufficient favorable movement)
        - OR trade is flat (price near entry)
        """
        if (
            self.pos.status != PositionStatus.OPEN
            or self.strategy.stale_max_minutes <= 0
        ):
            return None

        # Absolute stale exit (10% longer than normal stale time)
        absolute_stale_minutes = int(self.strategy.stale_max_minutes * 1.1)
        minutes_held = (
            datetime.now(timezone.utc) - self.pos.entry_time
        ).total_seconds() / 60

        if minutes_held >= absolute_stale_minutes:
            logger.warning(
                f"[{self.pos.symbol_id}] ABSOLUTE STALE EXIT triggered after "
                f"{absolute_stale_minutes}m - safety net activated"
            )
            return ExitCondition(
                "AbsoluteStaleExit",
                8,
                self.current_amount,
                self.current_price,
                f"Absolute stale exit after {absolute_stale_minutes}m (safety net)",
            )

        # Normal stale exit
        if minutes_held < self.strategy.stale_max_minutes:
            return None

        # Check for deferred exit (after breakeven promotion)
        defer_ts = self.pos.defer_stale_exit_until_ts
        if defer_ts and self.current_bar_ts < defer_ts:
            return None

        # Check exit conditions
        is_flat = abs(self.current_price - self.pos.entry_price) < (
            self.pos.initial_risk_atr * self.DECIMAL_POINT_ONE
        )
        should_exit = self.mfe_r < self.strategy.stale_min_mfe_r or (
            self.strategy.stale_exit_even_if_flat and is_flat
        )

        if should_exit:
            # If breakeven enforcement enabled and not yet promoted, defer exit
            if (
                self.strategy.stale_enforce_breakeven_before_exit
                and self.pos.progress_breakeven_eligible
                and defer_ts is None
            ):
                return None  # Trigger breakeven promotion outside engine

            return ExitCondition(
                "StaleTrade",
                8,
                self.current_amount,
                self.current_price,
                f"Stale trade exit after {self.strategy.stale_max_minutes}m",
            )

        return None

    # ==================== Priority 7: Advanced Exits ====================

    def _check_intrabar_mae_mfe(self) -> Optional[ExitCondition]:
        """
        Check intrabar MAE/MFE imbalance.

        NOTE: Currently disabled due to loss rate concerns.
        Exits when MAE/MFE ratio exceeds threshold for configured duration.
        """
        if self.mae_r < self.strategy.mae_intrabar_min_r:
            return None

        is_imbalanced = (
            self.mfe_r <= Decimal("0")
            or (self.mae_r / self.mfe_r) >= self.strategy.mae_intrabar_ratio_cutoff
        )

        if not is_imbalanced:
            # Clear breach timer if no longer imbalanced
            self.pos.intrabar_breach_started_at = None
            return None

        # Start timer on first breach
        current_time_sec = int(time.time())
        if self.pos.intrabar_breach_started_at is None:
            self.pos.intrabar_breach_started_at = current_time_sec
            return None

        # Check if hold duration met
        hold_seconds_elapsed = current_time_sec - self.pos.intrabar_breach_started_at
        if hold_seconds_elapsed >= self.strategy.mae_intrabar_hold_seconds:
            # Exit disabled - was causing premature exits
            pass

        return None

    def _check_bar_close_conditions(self) -> Optional[ExitCondition]:
        """
        Check bar-close dependent conditions (MAE/MFE ratio).

        NOTE: Currently disabled due to loss rate concerns.
        Only evaluates on new bar closes, not intrabar.
        """
        if self.current_bar_ts is None:
            return None

        # Only check on new bars
        last_ts = self.pos.last_checked_bar_ts
        if last_ts and self.current_bar_ts <= last_ts:
            return None

        # Check MAE/MFE imbalance over multiple bars
        if (
            not self._is_mae_suspended()
            and self.pos.bars_held >= self.strategy.min_bars_before_mfe_mae_cut
        ):

            is_imbalanced = self.mae_r >= self.strategy.mfe_mae_min_mae_r and (
                self.mfe_r / self.mae_r
                < (Decimal("1") / self.strategy.mfe_mae_ratio_cutoff)
                if self.mae_r > Decimal("0")
                else False
            )

            if is_imbalanced:
                self.pos.mae_breach_counter += 1

                if self.pos.mae_breach_counter >= self.strategy.mae_persist_bars:
                    # Exit disabled - was causing premature exits
                    logger.debug(
                        f"MaeMfeImbalance detected but disabled: "
                        f"{self.pos.mae_breach_counter} bars"
                    )
            else:
                self.pos.mae_breach_counter = 0

        return None

    # ==================== Helper Methods ====================

    def _is_minimum_hold_time_met(self) -> bool:
        """Check if minimum hold time requirement is met."""
        current_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        hold_duration_ms = current_ms - int(self.pos.entry_time.timestamp() * 1000)
        hold_duration_hours = hold_duration_ms / (1000 * 3600)

        min_hold_hours = float(self.strategy.min_hold_time_hours)

        # Double hold time in mean reversion regimes
        if self._is_mean_reversion_regime():
            min_hold_hours *= 2.0
            logger.debug(
                f"Mean reversion detected - extending hold time to {min_hold_hours:.1f}h"
            )

        min_hold_met = hold_duration_hours >= min_hold_hours
        if not min_hold_met:
            logger.debug(
                f"Minimum hold time not met: {hold_duration_hours:.2f}h < {min_hold_hours:.1f}h"
            )

        return min_hold_met

    def _is_mean_reversion_regime(self) -> bool:
        """Detect mean reversion regime (choppy, whipsaw conditions)."""
        if self.mfe_r > Decimal("0"):
            mae_mfe_ratio = self.mae_r / self.mfe_r
            is_mean_reversion = mae_mfe_ratio > self.strategy.mean_reversion_threshold
            if is_mean_reversion:
                logger.debug(
                    f"Mean reversion regime detected: MAE/MFE ratio {mae_mfe_ratio:.2f} > "
                    f"{self.strategy.mean_reversion_threshold}"
                )
            return is_mean_reversion
        return self.mae_r > self.DECIMAL_POINT_ONE

    def _is_mae_suspended(self) -> bool:
        """Check if MAE exits are suspended (after scale-out)."""
        suspend_until = self.pos.scaleout_suspend_until_bar_ts
        if suspend_until is None:
            return False
        return self.current_bar_ts < suspend_until

    def _get_effective_ratio_for_trailing(self) -> Decimal:
        """Get effective MFE/MAE ratio (post-TP1 if available, otherwise entry)."""
        if self.pos.tp1a_hit and self.pos.peak_favorable_r_beyond_tp1 > Decimal("0.1"):
            if self.pos.max_adverse_r_since_tp1_post > Decimal("0"):
                return (
                    self.pos.peak_favorable_r_beyond_tp1
                    / self.pos.max_adverse_r_since_tp1_post
                )

        # Fallback to entry ratio
        return (
            self.pos.mfe / self.pos.mae
            if self.pos.mae > Decimal("0")
            else Decimal("10.0")
        )
