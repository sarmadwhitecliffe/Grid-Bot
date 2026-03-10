"""
ExitConditionEngine extracted from old bot.py (proven battle-tested logic)
This is the exit condition evaluation engine that has run successfully for 4+ years.
"""

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from bot_v2.models.position_v1 import PositionState
    from bot_v2.models.strategy_config import StrategyConfig

logger = logging.getLogger(__name__)


class ExitCondition:
    """Represents a single exit condition with priority and details."""

    def __init__(
        self, name: str, priority: int, amount: Decimal, price: Decimal, reason: str
    ):
        self.name = name
        self.priority = priority  # Lower numbers = higher priority
        self.amount = amount
        self.price = price
        self.reason = reason


class ExitConditionEngine:
    """
    Centralized exit condition evaluation engine that implements the refactored
    exit precedence order and logic from the refactoring specifications.
    """

    DECIMAL_ZERO = Decimal("0")
    DECIMAL_ONE_E_MINUS_8 = Decimal("1e-8")
    DECIMAL_POINT_ONE = Decimal("0.1")

    def __init__(
        self,
        position: "PositionState",
        strategy: "StrategyConfig",
        current_price: Decimal,
        current_atr: Decimal,
        current_bar_ts: Optional[int] = None,
    ):
        self.pos = position
        self.strategy = strategy
        self.current_price = current_price
        self.current_atr = current_atr
        # Explicit check for None to allow 0 as valid timestamp
        self.current_bar_ts = (
            current_bar_ts if current_bar_ts is not None else int(time.time() * 1000)
        )

        # Calculate derived values
        self.pos.update_r_multiples(current_price)
        self.mfe_r, self.mae_r = self.pos.calculate_mfe_mae_r_multiples()
        self.side_buy = self.pos.side.value == "buy"
        self.current_amount = self.pos.current_amount

    def evaluate_all_exits(self) -> Optional["ExitCondition"]:
        """
        Evaluate all exit conditions in CORRECTED priority order for safety.
        """
        # Priority 1: Hard Stop Loss (immediate protection against catastrophic loss)
        catastrophic_exit = self._check_catastrophic_stop()
        if catastrophic_exit:
            return catastrophic_exit

        hard_stop_exit = self._check_hard_stop()
        if hard_stop_exit:
            return hard_stop_exit

        # Priority 2: Soft SL (SAFETY FIRST - must come before trailing stops!)
        soft_sl_exit = self._check_soft_sl_continuous()
        if soft_sl_exit:
            return soft_sl_exit

        # Priority 3: Trailing Stop/Breakeven (profit management - after safety)
        trailing_exit = self._check_trailing_stop()
        if trailing_exit:
            return trailing_exit

        # Priority 3.5: Minimum Hold Time Check (ONLY for profit-taking exits, NOT safety!)
        if not self._is_minimum_hold_time_met():
            # Allow safety exits to pass through, block only profit exits
            return None

        # Priority 3: Take Profit Target (profit booking)
        # CRITICAL: TP1 must be checked for both "open" and "partially_closed" status
        # - TP1a triggers when status is "open"
        # - TP1b triggers when status is "partially_closed" (after TP1a)
        tp1_exit = self._check_tp1()
        if tp1_exit:
            return tp1_exit

        # Priority 4: Adverse Event/Scale-out (risk reduction in unfavorable conditions)
        adverse_exit = self._check_adverse_scaleout()
        if adverse_exit:
            return adverse_exit

        # Priority 5: Time-based Exit (safety net after duration)
        stale_exit = self._check_stale_trade()
        if stale_exit:
            return stale_exit

        # Priority 6: Advanced/Discretionary exits (complex logic, lowest priority)
        # Intra-bar MAE/MFE exit (if not suspended)
        if not self._is_mae_suspended():
            intrabar_exit = self._check_intrabar_mae_mfe()
            if intrabar_exit:
                return intrabar_exit

        # Bar-close dependent checks (MAE/MFE only, soft SL moved to Priority 2)
        bar_close_exit = self._check_bar_close_conditions()
        if bar_close_exit:
            return bar_close_exit

        return None

    def _check_catastrophic_stop(self) -> Optional["ExitCondition"]:
        if (
            _to_decimal(
                self.strategy.catastrophic_stop_mult,
                "catastrophic_stop_mult",
                self.DECIMAL_ZERO,
            )
            <= self.DECIMAL_ZERO
        ):
            return None

        cat_stop_distance = self.pos.initial_risk_atr * _to_decimal(
            self.strategy.catastrophic_stop_mult,
            "catastrophic_stop_mult",
            self.DECIMAL_ZERO,
        )
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

    def _check_hard_stop(self) -> Optional["ExitCondition"]:
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

    def _is_minimum_hold_time_met(self) -> bool:
        # Using epoch milliseconds consistently
        current_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        hold_duration_ms = current_ms - int(self.pos.entry_time.timestamp() * 1000)
        hold_duration_hours = hold_duration_ms / (1000 * 3600)

        min_hold_hours = float(self.strategy.min_hold_time_hours)
        if self._is_mean_reversion_regime():
            min_hold_hours *= 2.0
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "Mean reversion detected - extending hold time to %.1fh",
                    min_hold_hours,
                )

        min_hold_met = hold_duration_hours >= min_hold_hours
        if not min_hold_met and logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "Minimum hold time not met: %.2fh < %.1fh",
                hold_duration_hours,
                min_hold_hours,
            )
        return min_hold_met

    def _is_mean_reversion_regime(self) -> bool:
        if self.mfe_r > self.DECIMAL_ZERO:
            mae_mfe_ratio = self.mae_r / self.mfe_r
            is_mean_reversion = mae_mfe_ratio > float(
                self.strategy.mean_reversion_threshold
            )
            if is_mean_reversion and logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "Mean reversion regime detected: MAE/MFE ratio %.2f > %s",
                    mae_mfe_ratio,
                    self.strategy.mean_reversion_threshold,
                )
            return is_mean_reversion
        return self.mae_r > self.DECIMAL_POINT_ONE

    def _is_mae_suspended(self) -> bool:
        suspend_until = getattr(self.pos, "scaleout_suspend_until_bar_ts", None)
        if suspend_until is None:
            return False
        return self.current_bar_ts < suspend_until

    def _check_intrabar_mae_mfe(self) -> Optional["ExitCondition"]:
        if self.mae_r < self.strategy.mae_intrabar_min_r:
            return None

        is_imbalanced = (
            self.mfe_r <= self.DECIMAL_ZERO
            or (self.mae_r / self.mfe_r) >= self.strategy.mae_intrabar_ratio_cutoff
        )
        if not is_imbalanced:
            if hasattr(self.pos, "intrabar_breach_started_at"):
                self.pos.intrabar_breach_started_at = None
            return None

        current_time_sec = int(time.time())
        if (
            not hasattr(self.pos, "intrabar_breach_started_at")
            or self.pos.intrabar_breach_started_at is None
        ):
            self.pos.intrabar_breach_started_at = current_time_sec
            return None

        hold_seconds_elapsed = current_time_sec - self.pos.intrabar_breach_started_at
        if hold_seconds_elapsed >= self.strategy.mae_intrabar_hold_seconds:
            (
                self.mae_r / self.mfe_r
                if self.mfe_r > self.DECIMAL_ZERO
                else Decimal("999")
            )
            # Clear note: This exit is currently skipped due to loss rate concerns
            # logger.info(f"Skipping MaeMfeImbalance exit (disabled): mae_r={self.mae_r:.2f}, mfe_r={self.mfe_r:.2f}, ratio={ratio:.2f}")
        return None

    def _check_tp1(self) -> Optional["ExitCondition"]:
        logger.debug(
            f"[{self.pos.symbol_id}] Checking TP1: tp1a_hit={getattr(self.pos, 'tp1a_hit', False)}, price={self.current_price}, tp1a_price={self.pos.tp1a_price}"
        )
        if not getattr(self.pos, "tp1a_hit", False) and self.pos.tp1a_price is not None:
            tp1a_triggered = (
                self.current_price >= self.pos.tp1a_price
                if self.side_buy
                else self.current_price <= self.pos.tp1a_price
            )
            if tp1a_triggered:
                close_amount = (
                    self.current_amount * (self.strategy.tp1a_close_percent / 100)
                ).quantize(self.DECIMAL_ONE_E_MINUS_8)
                return ExitCondition(
                    "TP1a",
                    4,
                    close_amount,
                    self.current_price,
                    f"TP1a scalp hit at {self.current_price:.4f} ({self.strategy.tp1a_close_percent:.0f}%)",
                )

        # CRITICAL: After TP1a, if trailing is active, let trailing manage the remaining position
        # TP1b should only trigger if trailing is NOT active (position hasn't reached trailing threshold)
        if getattr(self.pos, "tp1a_hit", False) and self.current_amount > 0:
            # Skip TP1b check if trailing is active - let trailing stop manage the exit
            if getattr(self.pos, "is_trailing_active", False):
                logger.debug(
                    f"[{self.pos.symbol_id}] TP1b check skipped - trailing is active, letting trailing manage exit"
                )
                return None

            tp1b_triggered = (
                self.current_price >= self.pos.tp1_price
                if self.side_buy
                else self.current_price <= self.pos.tp1_price
            )
            if tp1b_triggered:
                return ExitCondition(
                    "TP1b",
                    4,
                    self.current_amount,
                    self.current_price,
                    f"TP1b target hit at {self.current_price:.4f} (remaining position)",
                )
        return None

    def _check_adverse_scaleout(self) -> Optional["ExitCondition"]:
        if (
            self.pos.status.value != "open"
            or _to_decimal(
                self.strategy.partial_exit_on_adverse_r,
                "partial_exit_on_adverse_r",
                self.DECIMAL_ZERO,
            )
            <= self.DECIMAL_ZERO
            or getattr(self.pos, "scaled_out_on_adverse", False)
        ):
            return None

        # Debug logging for adverse scale-out evaluation
        threshold_r = _to_decimal(
            self.strategy.partial_exit_on_adverse_r,
            "partial_exit_on_adverse_r",
            self.DECIMAL_ZERO,
        )
        if self.mae_r >= threshold_r:
            if logger.isEnabledFor(logging.INFO):
                logger.info(
                    f"[{self.pos.symbol_id}] Adverse scale-out triggered: MAE {self.mae_r:.3f}R >= threshold {threshold_r:.3f}R"
                )
            close_amount = (
                self.current_amount * Decimal(self.strategy.partial_exit_pct) / 100
            ).quantize(self.DECIMAL_ONE_E_MINUS_8)
            return ExitCondition(
                "AdverseScaleOut",
                5,
                close_amount,
                self.current_price,
                f"Adverse scale-out at {self.mae_r:.2f}R drawdown",
            )
        elif self.mae_r > (
            threshold_r * Decimal("0.8")
        ):  # Log when approaching threshold
            logger.debug(
                f"[{self.pos.symbol_id}] Approaching adverse threshold: MAE {self.mae_r:.3f}R / {threshold_r:.3f}R"
            )

        return None

    def _check_trailing_stop(self) -> Optional["ExitCondition"]:
        # OPTION 5C + 5D: AGGRESSIVE PEAK EXIT (Hybrid with Adaptive Ratio)
        # Exit immediately on pullback from peak for clean trades (ratio >5)
        # Uses post-TP1 ratio if available (Option 5D), otherwise entry ratio (Option 5C)
        # CRITICAL: Only applies AFTER TP1a has been hit
        ratio, ratio_source = self.pos.get_effective_ratio_for_trailing()

        # Debug logging for ratio tracking
        if self.pos.tp1a_hit and logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "[%s] Ratio Check: ratio=%s (%s), MFE=%s, MAE=%s, peak_fav_r_tp1=%sR, max_adv_r_tp1=%sR",
                self.pos.symbol_id,
                str(ratio),
                ratio_source,
                str(self.pos.mfe),
                str(self.pos.mae),
                str(self.pos.peak_favorable_r_beyond_tp1),
                str(self.pos.max_adverse_r_since_tp1_post),
            )

        # CRITICAL FIX: Only check Aggressive Peak Exit AFTER TP1a has been hit
        # This ensures TP1a gets priority and executes first
        if self.pos.tp1a_hit and ratio > Decimal("5.0"):
            # Use post-TP1 R-multiples for peak/pullback if available to avoid premature exits
            use_post_tp1 = (
                getattr(self.strategy, "ape_use_post_tp1_measurement", True)
                and self.pos.tp1_ratio_reset_timestamp is not None
                and self.pos.peak_favorable_r_beyond_tp1 > Decimal("0.1")
                and self.pos.tp1a_price is not None
            )

            # Compute effective peak R and current R relative to TP1 when available
            if use_post_tp1:
                peak_r = self.pos.peak_favorable_r_beyond_tp1
                # Current favorable R since TP1
                if self.side_buy:
                    favorable_from_tp1 = max(
                        self.current_price - self.pos.tp1a_price, self.DECIMAL_ZERO
                    )
                else:
                    favorable_from_tp1 = max(
                        self.pos.tp1a_price - self.current_price, self.DECIMAL_ZERO
                    )
                current_r_eff = (
                    favorable_from_tp1 / self.pos.initial_risk_atr
                    if self.pos.initial_risk_atr > 0
                    else self.DECIMAL_ZERO
                )
                # Require a minimum development post-TP1 before allowing aggressive exit
                min_peak_required = Decimal("0.5")  # at least 0.5R progress after TP1
                # Also enforce a short grace period after TP1 to prevent instant exits
                grace_seconds = getattr(
                    self.strategy, "post_tp1_ape_grace_seconds", 120
                )
                tp1_age_seconds = (
                    datetime.now(timezone.utc)
                    - (self.pos.time_of_tp1 or self.pos.entry_time)
                ).total_seconds()
                if peak_r < min_peak_required or tp1_age_seconds < grace_seconds:
                    peak_r = None  # disable aggressive check for now
            else:
                peak_r = self.pos.peak_favorable_r
                current_r_eff = self.pos.current_r
                # Still require some progress from entry if post-TP1 data not available
                if peak_r < Decimal("0.3"):
                    peak_r = None

            if peak_r and peak_r > self.DECIMAL_ZERO:
                # Determine pullback threshold based on trade quality and time since TP1
                if ratio > Decimal("10.0"):
                    base_threshold = getattr(
                        self.strategy, "post_tp1_ape_base_pullback_pct", Decimal("0.03")
                    )
                    quality_tier = "Extreme"
                else:
                    base_threshold = getattr(
                        self.strategy, "post_tp1_ape_base_pullback_pct", Decimal("0.03")
                    )
                    quality_tier = "VeryClean"

                # Loosen threshold slightly in the first few minutes post-TP1
                if self.pos.tp1a_hit and self.pos.time_of_tp1 is not None:
                    minutes_since_tp1 = (
                        datetime.now(timezone.utc) - self.pos.time_of_tp1
                    ).total_seconds() / 60.0
                    initial_minutes = getattr(
                        self.strategy, "post_tp1_ape_initial_minutes", 3
                    )
                    initial_pullback = getattr(
                        self.strategy,
                        "post_tp1_ape_initial_pullback_pct",
                        Decimal("0.05"),
                    )
                    if minutes_since_tp1 < initial_minutes:
                        # Within the initial window after TP1, allow configurable pullback before exiting
                        base_threshold = max(base_threshold, initial_pullback)

                pullback_pct = (peak_r - current_r_eff) / peak_r
                if pullback_pct >= base_threshold:
                    if logger.isEnabledFor(logging.INFO):
                        logger.info(
                            "[%s] AGGRESSIVE PEAK EXIT (%s): Peak %sR → Current %sR (-%s%% >= -%s%% threshold) Ratio:%s (%s)",
                            self.pos.symbol_id,
                            quality_tier,
                            str(peak_r),
                            str(current_r_eff),
                            str(
                                (pullback_pct * 100)
                                if isinstance(pullback_pct, Decimal)
                                else Decimal(str(pullback_pct * 100))
                            ),
                            str(
                                (base_threshold * 100)
                                if isinstance(base_threshold, Decimal)
                                else Decimal(str(base_threshold * 100))
                            ),
                            str(ratio),
                            ratio_source,
                        )
                    return ExitCondition(
                        "AggressivePeakExit",
                        6,
                        self.current_amount,
                        self.current_price,
                        f"Aggressive peak exit at {self.current_price:.4f}",
                    )

        # Normal trailing stop check (fallback for all trades)
        if not self.pos.is_trailing_active or self.pos.trailing_sl_price is None:
            return None
        triggered = (
            self.current_price <= self.pos.trailing_sl_price
            if self.side_buy
            else self.current_price >= self.pos.trailing_sl_price
        )

        if triggered:
            return ExitCondition(
                "TrailExit",
                6,
                self.current_amount,
                self.current_price,
                f"Trailing stop at {self.current_price:.4f}",
            )
        return None

    def _check_soft_sl_continuous(self) -> Optional["ExitCondition"]:
        """
        FIXED: Continuous soft SL check without dangerous bars_held dependency.
        Safety stops should ALWAYS work regardless of timing or status!
        """
        if self.pos.soft_sl_price is None:
            return None

        # CRITICAL: Soft SL must work for BOTH open and partially_closed positions
        # After TP1a, soft SL moves to breakeven and should still protect the remaining position

        # CRITICAL FIX: Remove bars_held requirement for SAFETY
        # Original broken code: if self.pos.bars_held < self.strategy.soft_sl_activation_bars:
        #     return None

        # SAFETY FIRST: Soft SL should work immediately, only prevent immediate whipsaws
        current_time = datetime.now(timezone.utc)
        time_since_entry = current_time - self.pos.entry_time

        # Only 1 minute buffer to prevent whipsaws, then safety protection is active
        if time_since_entry.total_seconds() < 60:  # 1 minute minimum
            return None

        # GRACE PERIOD: After adverse scale-out, give position time to recover
        # Soft SL is moved to breakeven after scale-out, but we need grace period
        # to prevent immediate triggering if price is already below breakeven
        if getattr(self.pos, "scaled_out_on_adverse", False):
            scaleout_time = getattr(self.pos, "adverse_scaleout_timestamp", None)
            if scaleout_time:
                grace_seconds = getattr(
                    self.strategy, "scaleout_grace_period_seconds", 300
                )
                time_since_scaleout = (current_time - scaleout_time).total_seconds()
                if time_since_scaleout < grace_seconds:
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(
                            f"[{self.pos.symbol_id}] Soft SL check skipped: grace period active "
                            f"({time_since_scaleout:.0f}s / {grace_seconds}s since adverse scale-out)"
                        )
                    return None
                elif logger.isEnabledFor(logging.INFO):
                    logger.info(
                        f"[{self.pos.symbol_id}] Grace period expired: soft SL now active "
                        f"({time_since_scaleout:.0f}s since adverse scale-out)"
                    )

        soft_sl_hit = (
            self.current_price <= self.pos.soft_sl_price
            if self.side_buy
            else self.current_price >= self.pos.soft_sl_price
        )

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

    def _check_bar_close_conditions(self) -> Optional["ExitCondition"]:
        if self.current_bar_ts is None:
            return None

        last_ts = getattr(self.pos, "last_checked_bar_ts", None)
        if last_ts and self.current_bar_ts <= last_ts:
            return None

        # Using current price as bar close proxy as before

        if (
            not self._is_mae_suspended()
            and self.pos.bars_held >= self.strategy.min_bars_before_mfe_mae_cut
        ):

            mfe_r, mae_r = self.pos.calculate_mfe_mae_r_multiples()
            is_imbalanced = mae_r >= self.strategy.mfe_mae_min_mae_r and (
                mfe_r / mae_r < (1 / self.strategy.mfe_mae_ratio_cutoff)
                if mae_r > 0
                else False
            )

            if is_imbalanced:
                self.pos.mae_breach_counter = (
                    getattr(self.pos, "mae_breach_counter", 0) + 1
                )

                if self.pos.mae_breach_counter >= self.strategy.mae_persist_bars:
                    # MFE/MAE exit logic permanently disabled - was causing premature exits
                    logger.debug(
                        f"MaeMfeImbalance detected but disabled: {self.pos.mae_breach_counter} bars"
                    )
            else:
                self.pos.mae_breach_counter = 0
        return None

    def _check_stale_trade(self) -> Optional["ExitCondition"]:
        # FIXED: Stale check should work for both open and partially_closed positions
        # After TP1a, we still want to exit if the trade goes nowhere for too long
        if self.strategy.stale_max_minutes <= 0:
            return None

        # For partially closed positions, use 2x the stale timeout (already took some profit)
        stale_timeout = self.strategy.stale_max_minutes
        if self.pos.status.value == "partially_closed":
            stale_timeout = int(stale_timeout * 2)

        absolute_stale_minutes = int(self.strategy.stale_max_minutes * 1.1)
        if self.pos.is_stale(absolute_stale_minutes):
            if logger.isEnabledFor(logging.WARNING):
                logger.warning(
                    "[%s] ABSOLUTE STALE EXIT triggered after %sm - safety net activated",
                    self.pos.symbol_id,
                    absolute_stale_minutes,
                )
            return ExitCondition(
                "AbsoluteStaleExit",
                8,
                self.current_amount,
                self.current_price,
                f"Absolute stale exit after {absolute_stale_minutes}m (safety net)",
            )

        if not self.pos.is_stale(stale_timeout):
            return None

        defer_ts = getattr(self.pos, "defer_stale_exit_until_ts", None)
        if defer_ts and self.current_bar_ts < defer_ts:
            return None

        is_flat = abs(self.current_price - self.pos.entry_price) < (
            self.pos.initial_risk_atr * self.DECIMAL_POINT_ONE
        )
        should_exit = self.mfe_r < self.strategy.stale_min_mfe_r or (
            self.strategy.stale_exit_even_if_flat and is_flat
        )

        if should_exit:
            if (
                self.strategy.stale_enforce_breakeven_before_exit
                and self.pos.progress_breakeven_eligible
                and defer_ts is None
            ):
                # Trigger breakeven promotion outside this engine by returning None
                return None
            else:
                return ExitCondition(
                    "StaleTrade",
                    8,
                    self.current_amount,
                    self.current_price,
                    f"Stale trade exit after {self.strategy.stale_max_minutes}m",
                )
        return None


# Helper: _to_decimal stand-in (assuming it converts safely and logs warnings on failures)
def _to_decimal(value, name: str, default=Decimal("0")) -> Decimal:
    # Fast-path if already a Decimal
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(value)
    except Exception:
        logger.warning(
            f"Invalid decimal value for {name}: {value}, defaulting to {default}"
        )
        return default
