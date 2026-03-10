"""
Unit tests for trailing_stop.py

Tests trailing stop calculation, activation, and triggering logic
with comprehensive coverage of all quality tiers and edge cases.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot_v2.models.enums import PositionSide
from bot_v2.position.trailing_stop import TrailingStopCalculator, TrailingStopConfig


class TestTrailingStopActivation:
    """Test trailing stop activation logic."""

    def test_should_activate_when_r_exceeds_threshold(self, sample_long_position):
        """Trailing activates when current_r >= trailing_start_r."""
        pos = sample_long_position
        pos.current_r = Decimal("1.5")
        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        assert TrailingStopCalculator.should_activate_trailing(pos, config) is True

    def test_should_not_activate_when_r_below_threshold(self, sample_long_position):
        """Trailing does not activate when current_r < trailing_start_r."""
        pos = sample_long_position
        pos.current_r = Decimal("0.5")
        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        assert TrailingStopCalculator.should_activate_trailing(pos, config) is False

    def test_calculates_trail_safely_at_sub_one_r(self, sample_long_position):
        """Trailing calculation works when activated around 0.7R (pre-TP1)."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.current_r = Decimal("0.70")
        pos.peak_price_since_entry = Decimal("107.0")
        pos.mfe = Decimal("1.0")
        pos.mae = Decimal("1.0")

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("0.70")
        )

        result = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("107.0")
        )

        assert result.stop_price is not None
        assert result.stop_price < pos.peak_price_since_entry
        assert result.stop_price > Decimal("100.0")


class TestTrailingStopCalculation:
    """Test trailing stop price calculation."""

    def test_returns_none_when_not_active(self, sample_long_position):
        """Returns None if trailing not activated."""
        pos = sample_long_position
        pos.is_trailing_active = False
        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        result = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("0.05"), Decimal("100.0")
        )

        assert result.stop_price is None

    def test_long_position_trails_below_peak(self, sample_long_position):
        """LONG position: Stop trails below peak price."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.side = PositionSide.LONG
        pos.entry_price = Decimal("100.0")
        pos.peak_price_since_entry = Decimal("110.0")
        pos.entry_atr = Decimal("2.0")
        pos.trailing_sl_price = None
        pos.mfe = Decimal("0.0001")  # Minimal to avoid division by zero
        pos.mae = Decimal("0.0001")  # Minimal ratio = 1.0 (choppy)

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("110.0")
        )

        # Stop should be below peak
        # With choppy quality (ratio ~1.0), quality_mult ~= 1.2 (0.6 * base 2.0)
        # With early stage (R < 1.5), stage_mult ~= 1.6 (0.8 * base 2.0)
        # Weighted (70% Q, 30% S) = 1.32
        # Distance = 2.0 ATR * 1.32 = 2.64
        # Stop = 110 - 2.64 ~= 107.36
        assert new_stop.stop_price is not None
        assert new_stop.stop_price < pos.peak_price_since_entry
        assert new_stop.stop_price > Decimal("105.0")  # Reasonable range
        assert new_stop.stop_price < Decimal("108.0")  # Reasonable range

    def test_short_position_trails_above_peak(self, sample_short_position):
        """SHORT position: Stop trails above peak price."""
        pos = sample_short_position
        pos.is_trailing_active = True
        pos.side = PositionSide.SHORT
        pos.entry_price = Decimal("100.0")
        pos.peak_price_since_entry = Decimal("90.0")  # Lower is better for short
        pos.entry_atr = Decimal("2.0")
        pos.trailing_sl_price = None
        pos.mfe = Decimal("0.0001")  # Minimal to avoid division by zero
        pos.mae = Decimal("0.0001")  # Minimal ratio = 1.0 (choppy)

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("90.0")
        )

        # Stop should be above peak
        # With choppy quality, distance ~= 2.64 (same as LONG test)
        # Stop = 90 + 2.64 ~= 92.64
        assert new_stop.stop_price is not None
        assert new_stop.stop_price > pos.peak_price_since_entry
        assert new_stop.stop_price > Decimal("92.0")  # Reasonable range
        assert new_stop.stop_price < Decimal("95.0")  # Reasonable range

    def test_long_stop_only_moves_up(self, sample_long_position):
        """Trailing distance respects low R floor once trade > 1R."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.current_r = Decimal("1.5")
        pos.initial_risk_atr = Decimal("2.0")
        pos.peak_price_since_entry = Decimal("110.0")

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("1.0"),
            trailing_start_r=Decimal("0.5"),
            min_trailing_r_floor_low=Decimal("0.8"),  # 0.8R floor
            min_trailing_r_floor_high=Decimal("0.0"),
        )

        # ATR-based distance would be 1 * 2 = 2.0, floor is 0.8 * 2 = 1.6 → no change
        stop_price_result = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("110.0")
        )
        assert stop_price_result.stop_price is not None

        # Now lower ATR so ATR-based distance would be below floor (1.0)
        stop_price_floor = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("1.0"), Decimal("110.0")
        )
        assert stop_price_floor is not None
        # Distance from peak should not shrink below floor distance (0.8 * initial_risk_atr = 1.6)
        assert (pos.peak_price_since_entry - stop_price_floor.stop_price) >= Decimal(
            "1.6"
        )

    def test_trailing_respects_r_floor_low(self, sample_long_position):
        """Trailing distance respects low R floor once trade > 1R."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.current_r = Decimal("1.5")
        pos.initial_risk_atr = Decimal("2.0")
        pos.peak_price_since_entry = Decimal("110.0")

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("1.0"),
            trailing_start_r=Decimal("0.5"),
            min_trailing_r_floor_low=Decimal("0.8"),  # 0.8R floor
            min_trailing_r_floor_high=Decimal("0.0"),
        )

        # ATR-based distance would be 1 * 2 = 2.0, floor is 0.8 * 2 = 1.6 → no change
        stop_price_result = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("110.0")
        )
        assert stop_price_result.stop_price is not None

        # Now lower ATR so ATR-based distance would be below floor (1.0)
        stop_price_floor = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("1.0"), Decimal("110.0")
        )
        assert stop_price_floor is not None
        # Distance from peak should not shrink below floor distance (0.8 * initial_risk_atr = 1.6)
        assert (pos.peak_price_since_entry - stop_price_floor.stop_price) >= Decimal(
            "1.6"
        )
        """LONG: Stop only moves up (more protective), never down."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.side = PositionSide.LONG
        pos.entry_price = Decimal("100.0")
        pos.peak_price_since_entry = Decimal("110.0")
        pos.entry_atr = Decimal("2.0")
        pos.trailing_sl_price = Decimal("108.0")  # Existing stop

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal(
                "3.0"
            ),  # Would give 110 - 6 = 104 (lower than 108)
            trailing_start_r=Decimal("1.0"),
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("110.0")
        )

        # Should keep existing stop (108) since new stop (104) is lower
        assert new_stop.stop_price == Decimal("108.0")

    def test_short_stop_only_moves_down(self, sample_short_position):
        """SHORT: Stop only moves down (more protective), never up."""
        pos = sample_short_position
        pos.is_trailing_active = True
        pos.side = PositionSide.SHORT
        pos.entry_price = Decimal("100.0")
        pos.peak_price_since_entry = Decimal("90.0")
        pos.entry_atr = Decimal("2.0")
        pos.trailing_sl_price = Decimal("92.0")  # Existing stop

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("3.0"),  # Would give 90 + 6 = 96 (higher than 92)
            trailing_start_r=Decimal("1.0"),
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("90.0")
        )

        # Should keep existing stop (92) since new stop (96) is higher
        assert new_stop.stop_price == Decimal("92.0")


class TestTrailingStopTrigger:
    """Test trailing stop triggering detection."""

    def test_long_triggered_when_price_at_or_below_stop(self, sample_long_position):
        """LONG: Triggered when price <= trailing_sl_price."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.side = PositionSide.LONG
        pos.trailing_sl_price = Decimal("105.0")

        assert TrailingStopCalculator.is_stop_triggered(pos, Decimal("105.0")) is True
        assert TrailingStopCalculator.is_stop_triggered(pos, Decimal("104.0")) is True
        assert TrailingStopCalculator.is_stop_triggered(pos, Decimal("106.0")) is False

    def test_short_triggered_when_price_at_or_above_stop(self, sample_short_position):
        """SHORT: Triggered when price >= trailing_sl_price."""
        pos = sample_short_position
        pos.is_trailing_active = True
        pos.side = PositionSide.SHORT
        pos.trailing_sl_price = Decimal("95.0")

        assert TrailingStopCalculator.is_stop_triggered(pos, Decimal("95.0")) is True
        assert TrailingStopCalculator.is_stop_triggered(pos, Decimal("96.0")) is True
        assert TrailingStopCalculator.is_stop_triggered(pos, Decimal("94.0")) is False

    def test_not_triggered_when_trailing_inactive(self, sample_long_position):
        """Not triggered when trailing not active."""
        pos = sample_long_position
        pos.is_trailing_active = False
        pos.trailing_sl_price = Decimal("105.0")

        assert TrailingStopCalculator.is_stop_triggered(pos, Decimal("100.0")) is False

    def test_not_triggered_when_stop_price_none(self, sample_long_position):
        """Not triggered when trailing_sl_price is None."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.trailing_sl_price = None

        assert TrailingStopCalculator.is_stop_triggered(pos, Decimal("100.0")) is False


class TestQualityWeightedTrailing:
    """Test quality-weighted trailing adjustments."""

    def test_clean_trade_wider_trailing(self, sample_long_position):
        """Clean trade (high MFE/MAE) gets wider trailing distance."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.mfe = Decimal("10.0")
        pos.mae = Decimal("1.0")  # Ratio = 10 (extreme quality)
        pos.current_r = Decimal("2.0")
        pos.peak_price_since_entry = Decimal("110.0")
        pos.entry_atr = Decimal("2.0")
        pos.trailing_sl_price = None

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("110.0")
        )

        # Quality multiplier ~1.4x, stage multiplier ~1.2x, weighted ~1.36x
        # Distance should be > base (2.0 ATR * 2.0 = 4.0)
        assert new_stop.stop_price is not None
        distance = pos.peak_price_since_entry - new_stop.stop_price
        assert distance > Decimal("4.0")  # Wider than base

    def test_choppy_trade_tighter_trailing(self, sample_long_position):
        """Choppy trade (low MFE/MAE) gets tighter trailing distance."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.mfe = Decimal("1.0")
        pos.mae = Decimal("1.0")  # Ratio = 1 (choppy)
        pos.current_r = Decimal("0.5")
        pos.peak_price_since_entry = Decimal("105.0")
        pos.entry_atr = Decimal("2.0")
        pos.trailing_sl_price = None

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("105.0")
        )

        # Quality multiplier ~0.6x, weighted lower
        # Distance should be < base (2.0 ATR * 2.0 = 4.0)
        assert new_stop.stop_price is not None
        distance = pos.peak_price_since_entry - new_stop.stop_price
        assert distance < Decimal("4.0")  # Tighter than base


class TestPostTP1Probation:
    """Test post-TP1 probation logic."""

    def test_choppy_post_tp1_tight_protection(self, sample_long_position):
        """Choppy post-TP1 trade gets relaxed protection (0.50x)."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.tp1a_hit = True
        pos.post_tp1_probation_start = datetime.now(timezone.utc) - timedelta(
            minutes=0.5
        )  # Within 2min window
        pos.mfe = Decimal("2.0")
        pos.mae = Decimal("1.0")  # Ratio = 2.0 (choppy)
        pos.peak_favorable_r_beyond_tp1 = Decimal("1.0")
        pos.max_adverse_r_since_tp1_post = Decimal(
            "0.4"
        )  # Post-TP1 ratio = 2.5 (still choppy)
        pos.peak_price_since_entry = Decimal("110.0")
        pos.entry_atr = Decimal("2.0")
        pos.trailing_sl_price = None

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("110.0")
        )

        # Uses relaxed 0.50x probation multiplier (was 0.30x)
        # Distance = 2.0 ATR * 2.0 base * 0.50 = 2.0
        assert new_stop.stop_price is not None
        distance = pos.peak_price_since_entry - new_stop.stop_price
        assert abs(distance - Decimal("2.0")) < Decimal("0.1")

    def test_clean_post_tp1_loose_protection(self, sample_long_position):
        """Clean post-TP1 trade keeps relaxed protection (0.70x)."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.tp1a_hit = True
        pos.post_tp1_probation_start = datetime.now(timezone.utc) - timedelta(
            minutes=1
        )  # Within 2min window
        pos.mfe = Decimal("10.0")
        pos.mae = Decimal("1.0")  # Entry ratio = 10
        pos.peak_favorable_r_beyond_tp1 = Decimal("2.0")
        pos.max_adverse_r_since_tp1_post = Decimal(
            "0.3"
        )  # Post-TP1 ratio = 6.7 (clean)
        pos.peak_price_since_entry = Decimal("120.0")
        pos.entry_atr = Decimal("2.0")
        pos.trailing_sl_price = None

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("120.0")
        )

        # Uses relaxed 0.70x probation multiplier (was 0.40x)
        # Distance = 2.0 ATR * 2.0 base * 0.70 = 2.8
        assert new_stop.stop_price is not None
        distance = pos.peak_price_since_entry - new_stop.stop_price
        assert abs(distance - Decimal("2.8")) < Decimal("0.1")

    def test_weak_post_tp1_after_10_minutes(self, sample_long_position):
        """Weak trade (low ratio after 10 min) gets relaxed protection (0.60x)."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.tp1a_hit = True
        pos.post_tp1_probation_start = datetime.now(timezone.utc) - timedelta(
            minutes=15
        )
        pos.mfe = Decimal("2.0")
        pos.mae = Decimal("1.0")  # Entry ratio = 2.0
        pos.peak_favorable_r_beyond_tp1 = Decimal("0.5")
        pos.max_adverse_r_since_tp1_post = Decimal(
            "0.25"
        )  # Post-TP1 ratio = 2.0 (still low)
        pos.peak_price_since_entry = Decimal("110.0")
        pos.entry_atr = Decimal("2.0")
        pos.trailing_sl_price = None

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("110.0")
        )

        # Uses relaxed 0.60x weak post-TP1 multiplier (was 0.40x)
        # Distance = 2.0 ATR * 2.0 base * 0.60 = 2.4
        assert new_stop.stop_price is not None
        distance = pos.peak_price_since_entry - new_stop.stop_price
        assert abs(distance - Decimal("2.4")) < Decimal("0.1")


class TestRatioDecay:
    """Test ratio decay detection and override."""

    def test_extreme_quality_r_decay_15_percent(self, sample_long_position):
        """Extreme quality (ratio > 10): R-decay > 15% triggers 0.25x."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.mfe = Decimal("10.0")
        pos.mae = Decimal("0.8")  # Ratio = 12.5 (extreme)
        pos.current_r = Decimal("2.5")
        pos.peak_favorable_r = Decimal("3.0")  # 16.7% decay
        pos.peak_price_since_entry = Decimal("115.0")
        pos.entry_atr = Decimal("2.0")
        pos.trailing_sl_price = None

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("112.0")
        )

        # Should trigger 0.25x R-decay override
        # Distance = 2.0 ATR * 2.0 base * 0.25 = 1.0
        assert new_stop.stop_price is not None
        distance = pos.peak_price_since_entry - new_stop.stop_price
        assert abs(distance - Decimal("1.0")) < Decimal("0.1")

    def test_no_decay_when_peak_below_threshold(self, sample_long_position):
        """No decay override when peak_favorable_r below threshold."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.mfe = Decimal("10.0")
        pos.mae = Decimal("1.0")  # Ratio = 10
        pos.current_r = Decimal("0.1")
        pos.peak_favorable_r = Decimal("0.2")  # Below 0.30 threshold
        pos.peak_price_since_entry = Decimal("102.0")
        pos.entry_atr = Decimal("2.0")
        pos.trailing_sl_price = None

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("101.0")
        )

        # Should use normal trailing (no decay override)
        assert new_stop.stop_price is not None
        distance = pos.peak_price_since_entry - new_stop.stop_price
        # Normal quality/stage weighting, not 0.25x decay
        assert distance > Decimal("2.0")


class TestPostTP1ProbationCoverage:
    """Additional tests for post-TP1 probation logic (lines 339-385)."""

    def test_post_tp1_weak_trade_detection(self, sample_long_position):
        """Test weak post-TP1 detection after probation period."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.tp1a_hit = True
        pos.post_tp1_probation_start = datetime.now(timezone.utc) - timedelta(
            minutes=15
        )
        pos.peak_favorable_r_beyond_tp1 = Decimal("0.5")
        pos.max_adverse_r_since_tp1_post = Decimal("0.25")  # Ratio = 2.0 (weak)
        pos.peak_price_since_entry = Decimal("110.0")
        pos.entry_atr = Decimal("2.0")

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("110.0")
        )

        # Should apply tight weak-post-TP1 multiplier (0.40x)
        assert new_stop.stop_price is not None

    def test_early_probation_choppy_quality(self, sample_long_position):
        """Test quality-based probation in first 10 minutes with choppy ratio."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.tp1a_hit = True
        pos.post_tp1_probation_start = datetime.now(timezone.utc) - timedelta(minutes=5)
        pos.peak_favorable_r_beyond_tp1 = Decimal("0.5")
        pos.max_adverse_r_since_tp1_post = Decimal("0.2")  # Ratio = 2.5 (choppy)
        pos.peak_price_since_entry = Decimal("110.0")
        pos.entry_atr = Decimal("2.0")
        pos.current_r = Decimal("1.5")

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("110.0")
        )

        # Should apply probation multiplier for choppy quality (0.60x)
        assert new_stop.stop_price is not None

    def test_r_decay_extreme_quality(self, sample_long_position):
        """Test R-decay override with extreme quality ratio."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.peak_favorable_r = Decimal("10.0")  # Peak was 10R
        pos.current_r = Decimal("2.0")  # 80% decay
        pos.peak_price_since_entry = Decimal("120.0")
        pos.entry_atr = Decimal("2.0")
        pos.peak_favorable_r = Decimal("10.0")
        pos.max_adverse_r_since_entry = Decimal("0.5")  # Ratio = 20.0 (extreme)
        pos.mfe = Decimal("10.0")
        pos.mae = Decimal("0.5")

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("110.0")
        )

        # Apply state updates to position
        for update in new_stop.state_updates:
            pos = update.apply(pos)

        # Should apply R-decay override for extreme quality
        assert new_stop.stop_price is not None
        # Verify rdecay override was activated
        assert pos.rdecay_override_active is True

    def test_r_decay_very_clean_quality(self, sample_long_position):
        """Test R-decay override with very clean quality (ratio > 5)."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.peak_favorable_r = Decimal("5.0")  # Peak was 5R
        pos.current_r = Decimal("1.5")  # 70% decay
        pos.peak_price_since_entry = Decimal("110.0")
        pos.entry_atr = Decimal("2.0")
        pos.mfe = Decimal("5.0")
        pos.mae = Decimal("0.5")  # Ratio = 10.0 (very clean)

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("105.0")
        )

        # Apply state updates to position
        for update in new_stop.state_updates:
            pos = update.apply(pos)

        # Should apply very clean R-decay override
        assert new_stop.stop_price is not None
        assert pos.rdecay_override_active is True

    def test_price_decay_clean_quality(self, sample_long_position):
        """Test price decay override with clean quality (ratio > 2.5)."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.peak_favorable_r = Decimal("1.0")  # Above 0.30 threshold
        pos.current_r = Decimal("0.5")
        pos.peak_price_since_entry = Decimal("130.0")  # Peak price
        pos.entry_price = Decimal("100.0")
        pos.entry_atr = Decimal("2.0")
        pos.mfe = Decimal("3.0")
        pos.mae = Decimal("0.8")  # Ratio = 3.75 (clean)

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        # Current price at 95 is ~27% drop from peak of 130 (>25% threshold)
        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("95.0")
        )

        # Apply state updates to position
        for update in new_stop.state_updates:
            pos = update.apply(pos)

        # Should apply price decay override
        assert new_stop.stop_price is not None
        assert pos.rdecay_override_active is True

    def test_r_decay_moderate_quality(self, sample_long_position):
        """Test R-decay with moderate quality (ratio > 1.5)."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.peak_favorable_r = Decimal("2.0")  # Peak was 2R (above 0.30 threshold)
        pos.current_r = Decimal("0.8")  # 60% decay (>50% threshold)
        pos.peak_price_since_entry = Decimal("104.0")
        pos.entry_atr = Decimal("2.0")
        pos.mfe = Decimal("4.0")
        pos.mae = Decimal("2.0")  # Ratio = 2.0 (moderate, >1.5)

        config = TrailingStopConfig(
            trail_sl_atr_mult=Decimal("2.0"), trailing_start_r=Decimal("1.0")
        )

        new_stop = TrailingStopCalculator.calculate_trailing_stop(
            pos, config, Decimal("2.0"), Decimal("102.0")
        )

        # Apply state updates to position
        for update in new_stop.state_updates:
            pos = update.apply(pos)

        # Should apply moderate R-decay override
        assert new_stop.stop_price is not None
        assert pos.rdecay_override_active is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
