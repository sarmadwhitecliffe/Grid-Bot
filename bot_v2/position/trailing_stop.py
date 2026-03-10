"""
Trailing Stop Logic Module

Extracted from bot.py (lines 775-1074, 1364-1420, 3800-3950)
Provides trailing stop calculation, activation, and triggering logic
with ATR-based distance, ratio decay, and quality-weighted adjustments.

Key Features:
- ATR-based trailing distance (dynamic based on current volatility)
- Quality-weighted multipliers (MFE/MAE ratio adjustment)
- Stage-weighted multipliers (R-multiple based adjustment)
- Post-TP1 probation (quality-based protection after partial close)
- Ratio decay detection (locks in gains on momentum loss)
- Peak-reset protection (prevents ratchet effect)
- LONG/SHORT asymmetry (stops only move in protective direction)
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Tuple

from bot_v2.models.enums import PositionSide, PostTP1State
from bot_v2.models.position import DecayCache, Position
from bot_v2.utils.decimal_utils import safe_decimal
from bot_v2.utils.ratio_calculator import RatioCalculator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrailingStopResult:
    """Immutable result of trailing stop calculation."""

    stop_price: Optional[Decimal]
    state_updates: Tuple["StateUpdate", ...]  # Immutable tuple of state updates


@dataclass(frozen=True)
class RDecayCache:
    """Cached R-decay state to reduce expensive recalculations."""

    is_active: bool
    last_calculation_r: Decimal
    hysteresis_buffer: Decimal = Decimal("0.05")  # 5% hysteresis buffer

    def should_recalculate(self, current_r: Decimal) -> bool:
        """Check if decay state needs recalculation based on hysteresis."""
        if not self.is_active:
            # If inactive, recalculate if we've moved significantly toward activation
            return current_r < self.last_calculation_r * (
                Decimal("1") - self.hysteresis_buffer
            )
        else:
            # If active, recalculate if we've moved significantly toward deactivation
            return current_r > self.last_calculation_r * (
                Decimal("1") + self.hysteresis_buffer
            )


@dataclass(frozen=True)
class StateUpdate:
    """Represents a recommended state change for a position."""

    field_name: str
    new_value: Any
    reason: str

    def apply(self, position: Position) -> Position:
        """Apply this state update to a position, returning a new position."""
        return position.copy(**{self.field_name: self.new_value})


class StateUpdateBuilder:
    """Helper class to build state updates for trailing stop calculations."""

    @staticmethod
    def create_rdecay_activation(
        peak_r: Decimal, reason: str
    ) -> Tuple[StateUpdate, StateUpdate]:
        """Create state updates for R-decay activation."""
        return (
            StateUpdate("rdecay_override_active", True, reason),
            StateUpdate("last_rdecay_peak", peak_r, reason),
        )

    @staticmethod
    def create_rdecay_reset(reason: str) -> StateUpdate:
        """Create state update for R-decay reset."""
        return StateUpdate("rdecay_override_active", False, reason)


@dataclass
class TrailingStopConfig:
    """Configuration for trailing stop behavior."""

    trail_sl_atr_mult: Decimal  # Base ATR multiplier for trailing distance
    trailing_start_r: Decimal  # R-multiple at which trailing activates
    trailing_buffer_pct: Decimal = Decimal("0.5")  # Post-TP1 buffer percentage
    # Optional R-based trailing floors (in R, relative to initial risk)
    min_trailing_r_floor_low: Decimal = Decimal("0.0")
    min_trailing_r_floor_high: Decimal = Decimal("0.0")


class PostTP1StateMachine:
    """
    State machine for post-TP1 trailing behavior.

    Resolves logic conflicts by providing clear precedence rules:
    1. NOT_HIT: TP1a not triggered
    2. PROBATION: First 2 minutes after TP1a (highest priority)
    3. WEAK_TRADE: Probation expired with low quality ratio
    4. MOMENTUM_DECAY: Significant giveback from post-TP1 peak
    5. RATIO_DECAY: Ratio decay detected (locks in gains)
    6. NORMAL_TRAILING: Standard post-TP1 trailing
    """

    @staticmethod
    def get_state(position: Position, current_price: Decimal) -> PostTP1State:
        """
        Determine the current post-TP1 state with clear precedence.

        Args:
            position: Position object
            current_price: Current market price

        Returns the appropriate state based on position conditions.
        """
        # State 1: TP1a not hit
        if not position.tp1a_hit:
            return PostTP1State.NOT_HIT

        # State 2: Probation period (highest priority, overrides all others)
        if position.post_tp1_probation_start:
            time_since_tp1 = (
                datetime.now(timezone.utc) - position.post_tp1_probation_start
            ).total_seconds() / 60
            if time_since_tp1 < 2:  # 2-minute probation
                return PostTP1State.PROBATION

        # State 3: Weak trade detection (after probation)
        ratio, _ = RatioCalculator.get_ratio_for_trailing(position)
        if ratio < Decimal("3.0"):
            return PostTP1State.WEAK_TRADE

        # State 4: Momentum decay detection (post-TP1 peak giveback)
        if position.peak_favorable_r_beyond_tp1 > Decimal("0.5"):
            peak_total_r = Decimal("0.7") + position.peak_favorable_r_beyond_tp1
            decay_pct = ((peak_total_r - position.current_r) / peak_total_r) * 100
            if decay_pct > Decimal("10"):  # Either moderate or severe decay
                return PostTP1State.MOMENTUM_DECAY

        # State 5: Ratio decay detection (traditional R-decay)
        if PostTP1StateMachine._should_apply_ratio_decay(
            position, current_price, ratio
        ):
            return PostTP1State.RATIO_DECAY

        # State 6: Normal trailing (default post-TP1 state)
        return PostTP1State.NORMAL_TRAILING

    @staticmethod
    def get_multiplier_and_scheme(
        state: PostTP1State,
        position: Position,
        config: TrailingStopConfig,
        ratio: Decimal,
        ratio_source: str,
        current_price: Decimal,
    ) -> Tuple[Decimal, str, Tuple[StateUpdate, ...]]:
        """
        Get the appropriate multiplier and weighting scheme for the given state.

        Args:
            state: Current post-TP1 state
            position: Position object
            config: Trailing stop configuration
            ratio: Quality ratio for the position
            ratio_source: Source of the ratio ("entry" or "post-TP1")
            current_price: Current market price

        Returns:
            Tuple of (multiplier, weighting_scheme, state_updates)
        """
        if state == PostTP1State.NOT_HIT:
            # Not in post-TP1 logic, use normal trailing with ratio decay check
            mult, scheme = (
                TrailingStopCalculator._get_normal_trailing_multiplier(
                    ratio, position, config
                ),
                "NORMAL",
            )

            # Apply ratio decay override if detected
            decay_mult, decay_scheme, state_updates = (
                PostTP1StateMachine._check_ratio_decay_for_normal_trailing(
                    position, config, current_price, ratio
                )
            )
            if decay_mult != config.trail_sl_atr_mult:
                mult, scheme = decay_mult, decay_scheme

            return mult, scheme, state_updates

        elif state == PostTP1State.PROBATION:
            # Quality-based probation multipliers (relaxed from 0.30/0.35/0.40 on 2026-01-24)
            time_since_tp1 = (
                datetime.now(timezone.utc) - position.post_tp1_probation_start
            ).total_seconds() / 60

            if ratio < Decimal("3.0"):
                probation_mult = Decimal("0.50")  # CHOPPY (was 0.30)
                quality_tier = "CHOPPY"
            elif ratio < Decimal("5.0"):
                probation_mult = Decimal("0.60")  # MEDIUM (was 0.35)
                quality_tier = "MEDIUM"
            else:
                probation_mult = Decimal("0.70")  # CLEAN (was 0.40)
                quality_tier = "CLEAN"

            final_mult = config.trail_sl_atr_mult * probation_mult
            weighting_scheme = f"QUALITY-PROBATION-{quality_tier}"

            logger.info(
                f"[{position.symbol_id}] Quality-based probation at {position.current_r:.2f}R: "
                f"{time_since_tp1:.1f} min since TP1, ratio {ratio:.2f} ({quality_tier}) "
                f"→ {probation_mult}x mult ({ratio_source})"
            )

            return final_mult, weighting_scheme, ()

        elif state == PostTP1State.WEAK_TRADE:
            # Weak trade detection - tight stops (relaxed from 0.40 to 0.60 on 2026-01-24)
            final_mult = config.trail_sl_atr_mult * Decimal("0.60")
            weighting_scheme = "WEAK-POST-TP1"
            time_since_tp1 = (
                datetime.now(timezone.utc) - position.post_tp1_probation_start
            ).total_seconds() / 60

            # Only log if ratio or time changes significantly (ratio delta > 0.05 or time delta > 30 seconds)
            log_now = False
            current_time = time.time()

            if (
                position.last_weak_post_tp1_log_ratio is None
                or position.last_weak_post_tp1_log_timestamp is None
            ):
                log_now = True
            else:
                ratio_delta = abs(ratio - position.last_weak_post_tp1_log_ratio)
                time_delta = current_time - position.last_weak_post_tp1_log_timestamp
                if (
                    ratio_delta > 0.05 or time_delta > 30.0
                ):  # 30 seconds minimum interval
                    log_now = True

            if log_now:
                logger.warning(
                    f"[{position.symbol_id}] WEAK POST-TP1 DETECTED: "
                    f"Ratio {ratio:.2f} < 3.0 after {time_since_tp1:.1f} min post-TP1. "
                    f"Tightening trail to lock in gains (0.40x)."
                )
                # Store last log values in position (persistent)
                position.last_weak_post_tp1_log_ratio = ratio
                position.last_weak_post_tp1_log_timestamp = current_time

            return final_mult, weighting_scheme, ()

        elif state == PostTP1State.MOMENTUM_DECAY:
            # Momentum decay - very tight stops
            peak_total_r = Decimal("0.7") + position.peak_favorable_r_beyond_tp1
            decay_pct = ((peak_total_r - position.current_r) / peak_total_r) * 100

            if decay_pct > Decimal("15"):
                # Severe decay
                final_mult = config.trail_sl_atr_mult * Decimal("0.25")
                weighting_scheme = "SEVERE-DECAY"

                logger.warning(
                    f"[{position.symbol_id}] SEVERE MOMENTUM DECAY DETECTED: "
                    f"Peak {peak_total_r:.2f}R → Current {position.current_r:.2f}R "
                    f"({decay_pct:.1f}% decay). Very tight stop (0.25x)."
                )
            else:
                # Moderate decay
                final_mult = config.trail_sl_atr_mult * Decimal("0.35")
                weighting_scheme = "MODERATE-DECAY"

                logger.warning(
                    f"[{position.symbol_id}] MODERATE MOMENTUM DECAY: "
                    f"Peak {peak_total_r:.2f}R → Current {position.current_r:.2f}R "
                    f"({decay_pct:.1f}% decay). Tight stop (0.35x)."
                )

            return final_mult, weighting_scheme, ()

        elif state == PostTP1State.RATIO_DECAY:
            # Ratio decay - apply traditional R-decay logic
            mult, scheme, state_updates = (
                PostTP1StateMachine._get_ratio_decay_multiplier(
                    position, config, current_price, ratio
                )
            )
            return mult, scheme, state_updates

        elif state == PostTP1State.NORMAL_TRAILING:
            # Normal post-TP1 trailing - check for ratio decay
            mult, scheme = (
                TrailingStopCalculator._get_normal_trailing_multiplier(
                    ratio, position, config
                ),
                "NORMAL-POST-TP1",
            )

            # Apply ratio decay override if detected
            decay_mult, decay_scheme, state_updates = (
                PostTP1StateMachine._check_ratio_decay_for_normal_trailing(
                    position, config, current_price, ratio
                )
            )
            if decay_mult != config.trail_sl_atr_mult:
                mult, scheme = decay_mult, decay_scheme

            return mult, scheme, state_updates

        else:
            # Fallback - should never reach here
            logger.error(f"[{position.symbol_id}] Unknown PostTP1State: {state}")
            return config.trail_sl_atr_mult, "ERROR", ()

    @staticmethod
    def _should_apply_ratio_decay(
        position: Position, current_price: Decimal, ratio: Decimal
    ) -> bool:
        """Check if ratio decay should be applied (consolidated from old logic)."""
        # Calculate decay metrics
        price_decay_pct = TrailingStopCalculator._calculate_price_decay(
            position, current_price
        )
        r_decay_pct = TrailingStopCalculator._calculate_r_decay(position)

        # Use R-decay for clean trends, price decay for choppy
        decay_metric = r_decay_pct if ratio > Decimal("3.0") else price_decay_pct

        # Peak-reset protection
        if position.rdecay_override_active and position.last_rdecay_peak is not None:
            if TrailingStopCalculator._should_reset_rdecay(position):
                logger.info(
                    f"[{position.symbol_id}] Peak-Reset Protection: new peak {position.peak_favorable_r:.3f}R. "
                    f"R-DECAY override cleared, returning to normal trailing."
                )
                return False

        # Ratio decay thresholds
        decay_threshold = Decimal("0.15") if position.tp1a_hit else Decimal("0.30")

        # Apply decay if above threshold
        return position.peak_favorable_r > decay_threshold and decay_metric > Decimal(
            "10.0"
        )

    @staticmethod
    def _get_ratio_decay_multiplier(
        position: Position,
        config: TrailingStopConfig,
        current_price: Decimal,
        ratio: Decimal,
    ) -> Tuple[Decimal, str, Tuple[StateUpdate, ...]]:
        """Get multiplier for ratio decay state (consolidated from old logic)."""
        # Check cache first
        if TrailingStopCalculator._should_use_cached_decay(
            position, position.current_r
        ):
            cache = position.decay_cache
            logger.debug(
                f"[{position.symbol_id}] Using cached R-decay: {cache.decay_percentage:.1f}% → {cache.multiplier}x "
                f"(R: {cache.last_calculation_r:.2f}, hysteresis: {cache.hysteresis_lower_r:.2f}-{cache.hysteresis_upper_r:.2f})"
            )
            return cache.multiplier, f"CACHED-R-DECAY-{cache.decay_percentage:.0f}%", ()

        # Calculate decay metrics
        price_decay_pct = TrailingStopCalculator._calculate_price_decay(
            position, current_price
        )
        r_decay_pct = TrailingStopCalculator._calculate_r_decay(position)

        # Use R-decay for clean trends, price decay for choppy
        decay_metric = r_decay_pct if ratio > Decimal("3.0") else price_decay_pct

        # Set override flag
        state_updates = StateUpdateBuilder.create_rdecay_activation(
            position.peak_favorable_r,
            f"R-decay activation at {position.current_r:.2f}R",
        )

        # Apply ratio decay overrides based on quality tier
        multiplier = config.trail_sl_atr_mult
        scheme = "R-DECAY-FALLBACK"

        if ratio > Decimal("10.0"):  # Extreme quality
            if decay_metric > Decimal("15.0"):
                logger.warning(
                    f"[{position.symbol_id}] R-DECAY (Extreme): {decay_metric:.1f}% → 0.25x"
                )
                multiplier = config.trail_sl_atr_mult * Decimal("0.25")
                scheme = f"R-DECAY-{decay_metric:.0f}%"
            elif decay_metric > Decimal("10.0"):
                logger.debug(
                    f"[{position.symbol_id}] R-decay (extreme): {decay_metric:.1f}% → 0.35x"
                )
                multiplier = config.trail_sl_atr_mult * Decimal("0.35")
                scheme = f"R-DECAY-{decay_metric:.0f}%"

        elif ratio > Decimal("5.0"):  # Very clean quality
            if decay_metric > Decimal("20.0"):
                logger.warning(
                    f"[{position.symbol_id}] R-DECAY (Very Clean): {decay_metric:.1f}% → 0.30x"
                )
                multiplier = config.trail_sl_atr_mult * Decimal("0.30")
                scheme = f"R-DECAY-{decay_metric:.0f}%"
            elif decay_metric > Decimal("12.0"):
                logger.debug(
                    f"[{position.symbol_id}] R-decay (very clean): {decay_metric:.1f}% → 0.40x"
                )
                multiplier = config.trail_sl_atr_mult * Decimal("0.40")
                scheme = f"R-DECAY-{decay_metric:.0f}%"

        elif ratio > Decimal("2.5"):  # Clean quality
            if price_decay_pct > Decimal("25.0"):
                logger.warning(
                    f"[{position.symbol_id}] Price DECAY (Clean): {price_decay_pct:.1f}% → 0.35x"
                )
                multiplier = config.trail_sl_atr_mult * Decimal("0.35")
                scheme = f"P-DECAY-{price_decay_pct:.0f}%"
            elif price_decay_pct > Decimal("18.0"):
                logger.info(
                    f"[{position.symbol_id}] Price decay (clean): {price_decay_pct:.1f}% → 0.45x"
                )
                multiplier = config.trail_sl_atr_mult * Decimal("0.45")
                scheme = f"P-DECAY-{price_decay_pct:.0f}%"

        elif ratio > Decimal("1.5"):  # Moderate quality
            if r_decay_pct > Decimal("50.0"):
                logger.warning(
                    f"[{position.symbol_id}] R-DECAY (Moderate): {r_decay_pct:.1f}% → 0.40x"
                )
                multiplier = config.trail_sl_atr_mult * Decimal("0.40")
                scheme = f"R-DECAY-{r_decay_pct:.0f}%"
            elif r_decay_pct > Decimal("35.0"):
                logger.debug(
                    f"[{position.symbol_id}] R-decay (moderate): {r_decay_pct:.1f}% → 0.50x"
                )
                multiplier = config.trail_sl_atr_mult * Decimal("0.50")
                scheme = f"R-DECAY-{r_decay_pct:.0f}%"

        # Create cache and add to state updates
        cache = TrailingStopCalculator._create_decay_cache(
            decay_metric, multiplier, position.current_r, scheme, is_active=True
        )
        cache_update = StateUpdate(
            "decay_cache", cache, f"Cache R-decay: {decay_metric:.1f}% → {multiplier}x"
        )
        state_updates = state_updates + (cache_update,)

        return multiplier, scheme, state_updates

    @staticmethod
    def _check_ratio_decay_for_normal_trailing(
        position: Position,
        config: TrailingStopConfig,
        current_price: Decimal,
        ratio: Decimal,
    ) -> Tuple[Decimal, str, Tuple[StateUpdate, ...]]:
        """Check for ratio decay in normal trailing (non-TP1a positions)."""
        # Check cache first
        if (
            position.decay_cache is not None
            and TrailingStopCalculator._should_use_cached_decay(
                position, position.current_r
            )
        ):
            logger.debug(
                f"[{position.symbol_id}] Using cached R-decay result: {position.decay_cache.multiplier}x"
            )
            return position.decay_cache.multiplier, position.decay_cache.scheme, ()

        # Calculate decay metrics
        price_decay_pct = TrailingStopCalculator._calculate_price_decay(
            position, current_price
        )
        r_decay_pct = TrailingStopCalculator._calculate_r_decay(position)

        # Use R-decay for clean trends, price decay for choppy
        decay_metric = r_decay_pct if ratio > Decimal("3.0") else price_decay_pct

        # Peak-reset protection
        if position.rdecay_override_active and position.last_rdecay_peak is not None:
            if TrailingStopCalculator._should_reset_rdecay(position):
                logger.info(
                    f"[{position.symbol_id}] Peak-Reset Protection: new peak {position.peak_favorable_r:.3f}R. "
                    f"R-DECAY override cleared, returning to normal trailing."
                )
                return config.trail_sl_atr_mult, "NORMAL", ()

        # Ratio decay thresholds (higher for non-TP1a positions)
        decay_threshold = Decimal("0.30")  # Higher threshold for normal trailing

        # Check if any decay condition is met based on quality tier
        should_apply = False
        if ratio > Decimal("10.0"):  # Extreme quality
            should_apply = (
                position.peak_favorable_r > decay_threshold
                and decay_metric > Decimal("10.0")
            )
        elif ratio > Decimal("5.0"):  # Very clean quality
            should_apply = (
                position.peak_favorable_r > decay_threshold
                and decay_metric > Decimal("10.0")
            )
        elif ratio > Decimal("2.5"):  # Clean quality
            should_apply = (
                position.peak_favorable_r > decay_threshold
                and price_decay_pct > Decimal("15.0")
            )
        elif ratio > Decimal("1.5"):  # Moderate quality
            should_apply = (
                position.peak_favorable_r > decay_threshold
                and r_decay_pct > Decimal("35.0")
            )

        if should_apply:
            # Apply ratio decay overrides based on quality tier
            mult, scheme, state_updates = (
                PostTP1StateMachine._apply_ratio_decay_for_normal(
                    position, config, ratio, decay_metric, r_decay_pct, price_decay_pct
                )
            )
            # Create cache and add to state updates
            cache = TrailingStopCalculator._create_decay_cache(
                decay_metric, mult, position.current_r, scheme, is_active=True
            )
            cache_update = StateUpdate(
                "decay_cache", cache, f"Cache R-decay: {decay_metric:.1f}% → {mult}x"
            )
            state_updates = state_updates + (cache_update,)
            return mult, scheme, state_updates

        # No decay detected, create cache for no-decay state
        cache = TrailingStopCalculator._create_decay_cache(
            Decimal("0"),
            config.trail_sl_atr_mult,
            position.current_r,
            "NORMAL",
            is_active=False,
        )
        cache_update = StateUpdate("decay_cache", cache, "Cache no R-decay detected")
        return config.trail_sl_atr_mult, "NORMAL", (cache_update,)

    @staticmethod
    def _apply_ratio_decay_for_normal(
        position: Position,
        config: TrailingStopConfig,
        ratio: Decimal,
        decay_metric: Decimal,
        r_decay_pct: Decimal,
        price_decay_pct: Decimal,
    ) -> Tuple[Decimal, str, Tuple[StateUpdate, ...]]:
        """Apply ratio decay multiplier override for normal trailing."""
        # Set override flag
        state_updates = StateUpdateBuilder.create_rdecay_activation(
            position.peak_favorable_r,
            f"R-decay activation at {position.current_r:.2f}R",
        )

        # Same logic as post-TP1 but potentially different thresholds
        if ratio > Decimal("10.0"):  # Extreme quality
            if decay_metric > Decimal("15.0"):
                logger.warning(
                    f"[{position.symbol_id}] R-DECAY (Extreme): {decay_metric:.1f}% → 0.25x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.25"),
                    f"R-DECAY-{decay_metric:.0f}%",
                    state_updates,
                )
            elif decay_metric > Decimal("10.0"):
                logger.info(
                    f"[{position.symbol_id}] R-decay (extreme): {decay_metric:.1f}% → 0.35x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.35"),
                    f"R-DECAY-{decay_metric:.0f}%",
                    state_updates,
                )

        elif ratio > Decimal("5.0"):  # Very clean quality
            if decay_metric > Decimal("20.0"):
                logger.warning(
                    f"[{position.symbol_id}] R-DECAY (Very Clean): {decay_metric:.1f}% → 0.30x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.30"),
                    f"R-DECAY-{decay_metric:.0f}%",
                    state_updates,
                )
            elif decay_metric > Decimal("12.0"):
                logger.info(
                    f"[{position.symbol_id}] R-decay (very clean): {decay_metric:.1f}% → 0.40x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.40"),
                    f"R-DECAY-{decay_metric:.0f}%",
                    state_updates,
                )

        elif ratio > Decimal("2.5"):  # Clean quality
            if price_decay_pct > Decimal("25.0"):
                logger.warning(
                    f"[{position.symbol_id}] Price DECAY (Clean): {price_decay_pct:.1f}% → 0.35x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.35"),
                    f"P-DECAY-{price_decay_pct:.0f}%",
                    state_updates,
                )
            elif price_decay_pct > Decimal("18.0"):
                logger.info(
                    f"[{position.symbol_id}] Price decay (clean): {price_decay_pct:.1f}% → 0.45x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.45"),
                    f"P-DECAY-{price_decay_pct:.0f}%",
                    state_updates,
                )

        elif ratio > Decimal("1.5"):  # Moderate quality
            if r_decay_pct > Decimal("50.0"):
                logger.warning(
                    f"[{position.symbol_id}] R-DECAY (Moderate): {r_decay_pct:.1f}% → 0.40x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.40"),
                    f"R-DECAY-{r_decay_pct:.0f}%",
                    state_updates,
                )
            elif r_decay_pct > Decimal("35.0"):
                logger.info(
                    f"[{position.symbol_id}] R-decay (moderate): {r_decay_pct:.1f}% → 0.50x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.50"),
                    f"R-DECAY-{r_decay_pct:.0f}%",
                    state_updates,
                )

        # No decay detected, return original multiplier
        return config.trail_sl_atr_mult, "NO-DECAY", ()


class TrailingStopCalculator:
    """
    Calculates trailing stop prices with intelligent adaptation.

    Uses:
    - Current ATR for responsive trailing
    - Quality-weighted multipliers (MFE/MAE ratio)
    - Stage-weighted multipliers (R-multiple progress)
    - Post-TP1 probation (quality-based protection)
    - Ratio decay detection (momentum loss protection)
    - Peak-reset protection (prevents over-tightening)
    """

    @staticmethod
    def _should_use_cached_decay(position: Position, current_r: Decimal) -> bool:
        """
        Check if cached decay results can be used to avoid expensive recalculation.

        Returns True if the position has a valid cache and current R is within hysteresis bands.
        """
        if position.decay_cache is None:
            return False

        return not position.decay_cache.should_recalculate(current_r)

    @staticmethod
    def _create_decay_cache(
        decay_percentage: Decimal,
        multiplier: Decimal,
        current_r: Decimal,
        scheme: str = "",
        hysteresis_buffer_pct: Decimal = Decimal("5.0"),
        is_active: bool = True,
    ) -> DecayCache:
        """
        Create a new decay cache with hysteresis bands based on R-multiples.

        Args:
            decay_percentage: The calculated decay percentage
            multiplier: The trailing stop multiplier to cache
            current_r: Current R-multiple for hysteresis band calculation
            scheme: Description of the decay scheme applied
            hysteresis_buffer_pct: Percentage buffer for hysteresis bands (default 5%)
            is_active: Whether decay is currently active

        Returns:
            DecayCache with appropriate hysteresis bands for the current state
        """
        buffer_amount = current_r * (hysteresis_buffer_pct / Decimal("100"))
        return DecayCache(
            last_calculation_r=current_r,
            decay_percentage=decay_percentage,
            multiplier=multiplier,
            scheme=scheme,
            hysteresis_upper_r=current_r + buffer_amount,
            hysteresis_lower_r=current_r - buffer_amount,
            is_active=is_active,
        )

    @staticmethod
    def should_activate_trailing(
        position: Position, config: TrailingStopConfig
    ) -> bool:
        """Check if trailing stop should be activated."""
        return position.current_r >= config.trailing_start_r

    @staticmethod
    def calculate_trailing_stop(
        position: Position,
        config: TrailingStopConfig,
        current_atr: Decimal,
        current_price: Decimal,
    ) -> TrailingStopResult:
        """
        Calculate new trailing stop price with intelligent adaptation.

        Returns TrailingStopResult with new price and any state updates.
        Ensures stop only moves in protective direction (up for LONG, down for SHORT).
        """
        if not position.is_trailing_active:
            return TrailingStopResult(stop_price=None, state_updates=())

        # Use current ATR for responsive trailing, fallback to entry ATR
        trail_atr = (
            current_atr
            if current_atr and current_atr > Decimal("0")
            else position.entry_atr
        )

        # Calculate quality and stage multipliers
        quality_mult = TrailingStopCalculator._get_quality_adjusted_multiplier(
            position, config
        )
        stage_mult = TrailingStopCalculator._get_stage_adjusted_multiplier(
            position, config
        )

        # Get effective MFE/MAE ratio for trailing
        ratio, ratio_source = RatioCalculator.get_ratio_for_trailing(position)

        # Determine weighting scheme
        final_mult, weighting_scheme, state_updates = (
            TrailingStopCalculator._calculate_weighted_multiplier(
                position,
                config,
                quality_mult,
                stage_mult,
                ratio,
                ratio_source,
                current_price,
            )
        )

        # Note: Ratio decay override is now handled within the PostTP1StateMachine
        # to eliminate logic conflicts between probation and decay detection

        # Calculate trail distance (ATR-based)
        trail_distance = trail_atr * final_mult

        # Apply R-based trailing floors if configured
        # Floors expressed in R, using initial_risk_atr as 1R distance
        if position.initial_risk_atr and position.initial_risk_atr > Decimal("0"):
            floor_r = Decimal("0")
            if position.current_r >= Decimal("2.0"):
                floor_r = safe_decimal(
                    getattr(config, "min_trailing_r_floor_high", Decimal("0"))
                )
            elif position.current_r >= Decimal("1.0"):
                floor_r = safe_decimal(
                    getattr(config, "min_trailing_r_floor_low", Decimal("0"))
                )

            if floor_r > Decimal("0"):
                floor_distance = position.initial_risk_atr * floor_r
                if floor_distance > trail_distance:
                    trail_distance = floor_distance

        # Log intelligent adjustments (reduced frequency to prevent log spam)
        base_distance = trail_atr * config.trail_sl_atr_mult
        if abs(final_mult - config.trail_sl_atr_mult) > Decimal("0.01"):
            logger.debug(  # Changed from info to debug to reduce log volume
                f"[{position.symbol_id}] Intelligent trail at {position.current_r:.2f}R: "
                f"distance={trail_distance:.6f} (base: {base_distance:.6f}) | "
                f"mult: {final_mult:.3f}x (Q:{quality_mult:.3f} S:{stage_mult:.3f} "
                f"W:{weighting_scheme} ratio:{ratio:.2f})"
            )

        # Calculate new trailing stop price
        if position.side == PositionSide.LONG:
            # Trail below the peak price since entry
            new_trailing_sl = position.peak_price_since_entry - trail_distance
            # Only move up (more protective)
            if (
                position.trailing_sl_price is None
                or new_trailing_sl > position.trailing_sl_price
            ):
                return TrailingStopResult(
                    stop_price=new_trailing_sl, state_updates=state_updates
                )
        else:  # SHORT
            # Trail above the peak price since entry
            new_trailing_sl = position.peak_price_since_entry + trail_distance
            # Only move down (more protective)
            if (
                position.trailing_sl_price is None
                or new_trailing_sl < position.trailing_sl_price
            ):
                return TrailingStopResult(
                    stop_price=new_trailing_sl, state_updates=state_updates
                )

        # Keep existing trailing stop (no update needed)
        return TrailingStopResult(
            stop_price=position.trailing_sl_price, state_updates=state_updates
        )

    @staticmethod
    def is_stop_triggered(position: Position, current_price: Decimal) -> bool:
        """Check if current price has triggered the trailing stop."""
        if not position.is_trailing_active or position.trailing_sl_price is None:
            return False

        if position.side == PositionSide.LONG:
            return current_price <= position.trailing_sl_price
        else:  # SHORT
            return current_price >= position.trailing_sl_price

    # ==================== Private Helper Methods ====================

    @staticmethod
    def _get_quality_adjusted_multiplier(
        position: Position, config: TrailingStopConfig
    ) -> Decimal:
        """Calculate quality-weighted multiplier based on MFE/MAE ratio."""
        ratio = (
            position.mfe / position.mae
            if position.mae > Decimal("0")
            else Decimal("10.0")
        )

        # Quality tiers with multipliers
        if ratio > Decimal("10.0"):  # Extreme quality
            return config.trail_sl_atr_mult * Decimal("1.4")
        elif ratio > Decimal("5.0"):  # Very clean
            return config.trail_sl_atr_mult * Decimal("1.2")
        elif ratio > Decimal("3.0"):  # Clean
            return config.trail_sl_atr_mult * Decimal("1.0")
        elif ratio > Decimal("1.5"):  # Moderate
            return config.trail_sl_atr_mult * Decimal("0.8")
        else:  # Choppy
            return config.trail_sl_atr_mult * Decimal("0.6")

    @staticmethod
    def _get_stage_adjusted_multiplier(
        position: Position, config: TrailingStopConfig
    ) -> Decimal:
        """Calculate stage-weighted multiplier based on R-multiple progress."""
        if position.current_r > Decimal("3.0"):  # Large winner
            return config.trail_sl_atr_mult * Decimal("1.5")
        elif position.current_r > Decimal("2.0"):  # Good winner
            return config.trail_sl_atr_mult * Decimal("1.2")
        elif position.current_r > Decimal("1.0"):  # Profitable
            return config.trail_sl_atr_mult * Decimal("1.0")
        else:  # Early stage
            return config.trail_sl_atr_mult * Decimal("0.8")

    # NOTE: _get_effective_ratio_for_trailing has been moved to RatioCalculator.get_ratio_for_trailing()
    # for consistent ratio calculations across the system (Issue 5: Inconsistent Ratio Calculations)

    @staticmethod
    def _calculate_weighted_multiplier(
        position: Position,
        config: TrailingStopConfig,
        quality_mult: Decimal,
        stage_mult: Decimal,
        ratio: Decimal,
        ratio_source: str,
        current_price: Decimal,
    ) -> Tuple[Decimal, str, Tuple[StateUpdate, ...]]:
        """
        Calculate weighted multiplier using PostTP1StateMachine.

        This method now uses the state machine to resolve logic conflicts
        and provide clear precedence rules for post-TP1 trailing behavior.
        """
        # Get current post-TP1 state
        state = PostTP1StateMachine.get_state(position, current_price)

        # Get multiplier and scheme from state machine
        return PostTP1StateMachine.get_multiplier_and_scheme(
            state, position, config, ratio, ratio_source, current_price
        )

    @staticmethod
    def _get_normal_trailing_multiplier(
        ratio: Decimal, position: Position, config: TrailingStopConfig
    ) -> Decimal:
        """
        Get normal trailing multiplier using full weighted logic.

        This replicates the original weighted calculation for normal trailing
        (when not in post-TP1 states) to maintain backward compatibility.
        """
        # Get quality and stage multipliers
        quality_mult = TrailingStopCalculator._get_quality_adjusted_multiplier(
            position, config
        )
        stage_mult = TrailingStopCalculator._get_stage_adjusted_multiplier(
            position, config
        )

        # Apply weighting based on current R-multiple
        if position.current_r < Decimal("1.0"):
            weight_q, weight_s = Decimal("0.85"), Decimal("0.15")
        elif position.current_r < Decimal("1.5"):
            weight_q, weight_s = Decimal("0.70"), Decimal("0.30")
        else:
            weight_q, weight_s = Decimal("0.60"), Decimal("0.40")

        return quality_mult * weight_q + stage_mult * weight_s

    @staticmethod
    def _check_ratio_decay_override(
        position: Position,
        config: TrailingStopConfig,
        current_price: Decimal,
        ratio: Decimal,
        final_mult: Decimal,
        weighting_scheme: str,
    ) -> Tuple[Decimal, str]:
        """Check for ratio decay and apply override if detected."""
        # Calculate decay metrics
        price_decay_pct = TrailingStopCalculator._calculate_price_decay(
            position, current_price
        )
        r_decay_pct = TrailingStopCalculator._calculate_r_decay(position)

        # Use R-decay for clean trends, price decay for choppy
        decay_metric = r_decay_pct if ratio > Decimal("3.0") else price_decay_pct

        # Peak-reset protection
        if position.rdecay_override_active and position.last_rdecay_peak is not None:
            if TrailingStopCalculator._should_reset_rdecay(position):
                logger.info(
                    f"[{position.symbol_id}] Peak-Reset Protection: new peak {position.peak_favorable_r:.3f}R. "
                    f"R-DECAY override cleared, returning to normal trailing."
                )
                return final_mult, weighting_scheme

        # Ratio decay thresholds
        decay_threshold = Decimal("0.15") if position.tp1a_hit else Decimal("0.30")

        # Skip ratio decay during choppy probation (already protected)
        skip_rdecay = (
            position.tp1a_hit
            and position.post_tp1_probation_start
            and (
                datetime.now(timezone.utc) - position.post_tp1_probation_start
            ).total_seconds()
            / 60
            < 10
            and ratio < Decimal("3.0")
        )

        if position.peak_favorable_r > decay_threshold and not skip_rdecay:
            # Apply ratio decay overrides based on quality tier
            return TrailingStopCalculator._apply_ratio_decay(
                position, config, ratio, decay_metric, r_decay_pct, price_decay_pct
            )

        return final_mult, weighting_scheme

    @staticmethod
    def _calculate_price_decay(position: Position, current_price: Decimal) -> Decimal:
        """Calculate percentage price decay from peak."""
        if position.peak_price_since_entry <= Decimal("0"):
            return Decimal("0")

        if position.side == PositionSide.LONG:
            decay = (
                (position.peak_price_since_entry - current_price)
                / position.peak_price_since_entry
                * Decimal("100")
            )
        else:  # SHORT
            decay = (
                (current_price - position.peak_price_since_entry)
                / position.peak_price_since_entry
                * Decimal("100")
            )

        return max(decay, Decimal("0"))

    @staticmethod
    def _calculate_r_decay(position: Position) -> Decimal:
        """Calculate percentage R-multiple decay from peak."""
        if position.peak_favorable_r <= Decimal("0.1"):
            return Decimal("0")

        decay = (
            (position.peak_favorable_r - position.current_r)
            / position.peak_favorable_r
            * Decimal("100")
        )
        return max(decay, Decimal("0"))

    @staticmethod
    def _should_reset_rdecay(position: Position) -> bool:
        """Check if R-decay override should be reset due to new peak."""
        if position.last_rdecay_peak is None or position.last_rdecay_peak <= Decimal(
            "0"
        ):
            return False

        peak_increase_pct = (
            (position.peak_favorable_r - position.last_rdecay_peak)
            / position.last_rdecay_peak
            * Decimal("100")
        )

        return peak_increase_pct > Decimal("5.0") and position.current_r > (
            position.peak_favorable_r * Decimal("0.90")
        )

    @staticmethod
    def _apply_ratio_decay(
        position: Position,
        config: TrailingStopConfig,
        ratio: Decimal,
        decay_metric: Decimal,
        r_decay_pct: Decimal,
        price_decay_pct: Decimal,
    ) -> Tuple[Decimal, str]:
        """Apply ratio decay multiplier override based on quality tier."""
        # Extreme quality (ratio > 10)
        if ratio > Decimal("10.0"):
            if decay_metric > Decimal("15.0"):
                position.rdecay_override_active = True
                position.last_rdecay_peak = position.peak_favorable_r
                logger.warning(
                    f"[{position.symbol_id}] R-DECAY (Extreme): {decay_metric:.1f}% → 0.25x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.25"),
                    f"R-DECAY-{decay_metric:.0f}%",
                )
            elif decay_metric > Decimal("10.0"):
                position.rdecay_override_active = True
                position.last_rdecay_peak = position.peak_favorable_r
                logger.info(
                    f"[{position.symbol_id}] R-decay (extreme): {decay_metric:.1f}% → 0.35x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.35"),
                    f"R-DECAY-{decay_metric:.0f}%",
                )

        # Very clean quality (ratio > 5)
        elif ratio > Decimal("5.0"):
            if decay_metric > Decimal("20.0"):
                position.rdecay_override_active = True
                position.last_rdecay_peak = position.peak_favorable_r
                logger.warning(
                    f"[{position.symbol_id}] R-DECAY (Very Clean): {decay_metric:.1f}% → 0.30x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.30"),
                    f"R-DECAY-{decay_metric:.0f}%",
                )
            elif decay_metric > Decimal("12.0"):
                position.rdecay_override_active = True
                position.last_rdecay_peak = position.peak_favorable_r
                logger.info(
                    f"[{position.symbol_id}] R-decay (very clean): {decay_metric:.1f}% → 0.40x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.40"),
                    f"R-DECAY-{decay_metric:.0f}%",
                )

        # Clean quality (ratio > 2.5)
        elif ratio > Decimal("2.5"):
            if price_decay_pct > Decimal("25.0"):
                position.rdecay_override_active = True
                position.last_rdecay_peak = position.peak_favorable_r
                logger.warning(
                    f"[{position.symbol_id}] Price DECAY (Clean): {price_decay_pct:.1f}% → 0.35x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.35"),
                    f"P-DECAY-{price_decay_pct:.0f}%",
                )
            elif price_decay_pct > Decimal("18.0"):
                position.rdecay_override_active = True
                position.last_rdecay_peak = position.peak_favorable_r
                logger.info(
                    f"[{position.symbol_id}] Price decay (clean): {price_decay_pct:.1f}% → 0.45x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.45"),
                    f"P-DECAY-{price_decay_pct:.0f}%",
                )

        # Moderate quality (ratio > 1.5)
        elif ratio > Decimal("1.5"):
            if r_decay_pct > Decimal("50.0"):
                position.rdecay_override_active = True
                position.last_rdecay_peak = position.peak_favorable_r
                logger.warning(
                    f"[{position.symbol_id}] R-DECAY (Moderate): {r_decay_pct:.1f}% → 0.40x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.40"),
                    f"R-DECAY-{r_decay_pct:.0f}%",
                )
            elif r_decay_pct > Decimal("35.0"):
                position.rdecay_override_active = True
                position.last_rdecay_peak = position.peak_favorable_r
                logger.info(
                    f"[{position.symbol_id}] R-decay (moderate): {r_decay_pct:.1f}% → 0.50x"
                )
                return (
                    config.trail_sl_atr_mult * Decimal("0.50"),
                    f"R-DECAY-{r_decay_pct:.0f}%",
                )

        # No decay detected, return original multiplier
        return config.trail_sl_atr_mult, "NO-DECAY"
