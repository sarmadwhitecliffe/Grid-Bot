"""
Tests for position.tracker module.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bot_v2.models.enums import PositionSide, PositionStatus
from bot_v2.models.position import Position
from bot_v2.position.tracker import PositionQualityAnalyzer, PositionTracker


@pytest.fixture
def long_position():
    """Create a sample LONG position."""
    return Position(
        symbol_id="BTCUSDT",
        side=PositionSide.LONG,
        entry_price=Decimal("50000"),
        entry_time=datetime.now(timezone.utc),
        initial_amount=Decimal("0.01"),
        entry_atr=Decimal("500"),
        initial_risk_atr=Decimal("250"),
        soft_sl_price=Decimal("49500"),
        hard_sl_price=Decimal("49000"),
        tp1_price=Decimal("51000"),
        total_entry_fee=Decimal("0.5"),
    )


@pytest.fixture
def short_position():
    """Create a sample SHORT position."""
    return Position(
        symbol_id="ETHUSDT",
        side=PositionSide.SHORT,
        entry_price=Decimal("3000"),
        entry_time=datetime.now(timezone.utc),
        initial_amount=Decimal("1.0"),
        entry_atr=Decimal("50"),
        initial_risk_atr=Decimal("25"),
        soft_sl_price=Decimal("3050"),
        hard_sl_price=Decimal("3100"),
        tp1_price=Decimal("2900"),
        total_entry_fee=Decimal("3.0"),
    )


class TestMFEMAETracking:
    """Test MFE/MAE tracking functionality."""

    def test_long_position_favorable_move(self, long_position):
        """Test MFE tracking on favorable price movement (LONG)."""
        # Price moves up (favorable for LONG)
        updated = PositionTracker.update_mfe_mae(long_position, Decimal("51000"))

        assert updated.mfe == Decimal("1000")  # 51000 - 50000
        assert updated.mae == Decimal("0")  # No adverse movement
        assert updated.peak_price_since_entry == Decimal("51000")

    def test_long_position_adverse_move(self, long_position):
        """Test MAE tracking on adverse price movement (LONG)."""
        # Price moves down (adverse for LONG)
        updated = PositionTracker.update_mfe_mae(long_position, Decimal("49500"))

        assert updated.mfe == Decimal("0")  # No favorable movement
        assert updated.mae == Decimal("500")  # 50000 - 49500
        assert updated.peak_price_since_entry == Decimal("50000")  # Entry price

    def test_short_position_favorable_move(self, short_position):
        """Test MFE tracking on favorable price movement (SHORT)."""
        # Price moves down (favorable for SHORT)
        updated = PositionTracker.update_mfe_mae(short_position, Decimal("2900"))

        assert updated.mfe == Decimal("100")  # 3000 - 2900
        assert updated.mae == Decimal("0")  # No adverse movement
        assert updated.peak_price_since_entry == Decimal("2900")

    def test_short_position_adverse_move(self, short_position):
        """Test MAE tracking on adverse price movement (SHORT)."""
        # Price moves up (adverse for SHORT)
        updated = PositionTracker.update_mfe_mae(short_position, Decimal("3100"))

        assert updated.mfe == Decimal("0")  # No favorable movement
        assert updated.mae == Decimal("100")  # 3100 - 3000
        assert updated.peak_price_since_entry == Decimal("3000")  # Entry price

    def test_mfe_mae_persists_highest_values(self, long_position):
        """Test that MFE/MAE persist the highest values seen."""
        # First move up
        pos = PositionTracker.update_mfe_mae(long_position, Decimal("51000"))
        assert pos.mfe == Decimal("1000")

        # Price retraces down
        pos = PositionTracker.update_mfe_mae(pos, Decimal("49000"))
        assert pos.mfe == Decimal("1000")  # MFE persists
        assert pos.mae == Decimal("1000")  # MAE updated

        # Price recovers
        pos = PositionTracker.update_mfe_mae(pos, Decimal("50500"))
        assert pos.mfe == Decimal("1000")  # Still the peak
        assert pos.mae == Decimal("1000")  # Still the worst

    def test_current_ratio_calculation(self, long_position):
        """Test current ratio (MFE/MAE) calculation."""
        # Move up then down
        pos = PositionTracker.update_mfe_mae(
            long_position, Decimal("52000")
        )  # +2000 MFE
        pos = PositionTracker.update_mfe_mae(pos, Decimal("49000"))  # +1000 MAE

        # Ratio should be 2000 / 1000 = 2.0
        assert pos.current_ratio == Decimal("2.0")

    def test_current_ratio_extremely_clean(self, long_position):
        """Test ratio capped at 50 for extremely clean moves."""
        # Only favorable movement, virtually no adverse
        pos = PositionTracker.update_mfe_mae(long_position, Decimal("51000"))

        # MAE < 0.1% of entry, ratio should be capped at 50
        assert pos.current_ratio == Decimal("50.0")


class TestRMultipleTracking:
    """Test R-multiple tracking functionality."""

    def test_long_position_positive_r(self, long_position):
        """Test R-multiple calculation for profitable LONG."""
        # Price up $500 = 2R (initial_risk_atr = $250)
        updated = PositionTracker.update_r_multiples(long_position, Decimal("50500"))

        assert updated.current_r == Decimal("2.0")  # 500 / 250
        assert updated.peak_favorable_r == Decimal("2.0")
        assert updated.peak_adverse_r == Decimal("0")

    def test_long_position_negative_r(self, long_position):
        """Test R-multiple calculation for losing LONG."""
        # Price down $250 = -1R
        updated = PositionTracker.update_r_multiples(long_position, Decimal("49750"))

        assert updated.current_r == Decimal("-1.0")  # -250 / 250
        assert updated.peak_favorable_r == Decimal("0")
        assert updated.peak_adverse_r == Decimal("1.0")  # Absolute value

    def test_short_position_positive_r(self, short_position):
        """Test R-multiple calculation for profitable SHORT."""
        # Price down $50 = 2R (initial_risk_atr = $25)
        updated = PositionTracker.update_r_multiples(short_position, Decimal("2950"))

        assert updated.current_r == Decimal("2.0")  # 50 / 25
        assert updated.peak_favorable_r == Decimal("2.0")

    def test_r_multiple_peak_tracking(self, long_position):
        """Test that peak R values are tracked correctly."""
        # Move to +2R
        pos = PositionTracker.update_r_multiples(long_position, Decimal("50500"))
        assert pos.peak_favorable_r == Decimal("2.0")

        # Retrace to -1R
        pos = PositionTracker.update_r_multiples(pos, Decimal("49750"))
        assert pos.peak_favorable_r == Decimal("2.0")  # Peak persists
        assert pos.peak_adverse_r == Decimal("1.0")  # Adverse tracked

        # Move to +3R
        pos = PositionTracker.update_r_multiples(pos, Decimal("50750"))
        assert pos.peak_favorable_r == Decimal("3.0")  # New peak
        assert pos.peak_adverse_r == Decimal("1.0")  # Adverse persists

    def test_max_adverse_r_since_entry(self, long_position):
        """Test max_adverse_r_since_entry tracking."""
        # Move to -0.5R
        pos = PositionTracker.update_r_multiples(long_position, Decimal("49875"))
        assert pos.max_adverse_r_since_entry == Decimal("0.5")

        # Move to -1.5R (worse)
        pos = PositionTracker.update_r_multiples(pos, Decimal("49625"))
        assert pos.max_adverse_r_since_entry == Decimal("1.5")

        # Recover to +2R
        pos = PositionTracker.update_r_multiples(pos, Decimal("50500"))
        assert pos.max_adverse_r_since_entry == Decimal("1.5")  # Persists

    def test_max_adverse_r_after_partial_close(self, long_position):
        """Test max_adverse_r_since_tp1 only tracks after partial close."""
        # Not partially closed yet
        pos = PositionTracker.update_r_multiples(long_position, Decimal("49750"))
        assert pos.max_adverse_r_since_tp1 == Decimal("0")

        # Partially close
        pos = pos.copy(status=PositionStatus.PARTIALLY_CLOSED)

        # Now adverse R should track in tp1 field
        pos = PositionTracker.update_r_multiples(pos, Decimal("49750"))
        assert pos.max_adverse_r_since_tp1 == Decimal("1.0")


class TestPostTP1Metrics:
    """Test post-TP1 quality metrics."""

    def test_post_tp1_not_tracked_before_tp1_hit(self, long_position):
        """Test post-TP1 metrics not updated before TP1 hit."""
        updated = PositionTracker.update_post_tp1_metrics(
            long_position, Decimal("51000")
        )

        # Should return unchanged (TP1 not hit)
        assert (
            updated.peak_favorable_r_beyond_tp1
            == long_position.peak_favorable_r_beyond_tp1
        )
        assert updated.ratio_since_tp1 == long_position.ratio_since_tp1

    def test_post_tp1_favorable_tracking(self, long_position):
        """Test favorable movement tracking after TP1."""
        # Mark TP1 hit
        pos = long_position.copy(
            tp1a_hit=True,
            tp1a_price=Decimal("51000"),
            tp1_ratio_reset_timestamp=datetime.now(timezone.utc),
        )

        # Price moves to 51500 (500 beyond TP1 = 2R)
        updated = PositionTracker.update_post_tp1_metrics(pos, Decimal("51500"))

        assert updated.peak_favorable_r_beyond_tp1 == Decimal("2.0")  # 500 / 250

    def test_post_tp1_adverse_tracking(self, long_position):
        """Test adverse movement tracking after TP1."""
        # Mark TP1 hit
        pos = long_position.copy(
            tp1a_hit=True,
            tp1a_price=Decimal("51000"),
            tp1_ratio_reset_timestamp=datetime.now(timezone.utc),
        )

        # Price retraces to 50750 (250 below TP1 = 1R adverse)
        updated = PositionTracker.update_post_tp1_metrics(pos, Decimal("50750"))

        assert updated.max_adverse_r_since_tp1_post == Decimal("1.0")  # 250 / 250

    def test_post_tp1_ratio_calculation(self, long_position):
        """Test ratio_since_tp1 calculation."""
        # Mark TP1 hit
        pos = long_position.copy(
            tp1a_hit=True,
            tp1a_price=Decimal("51000"),
            tp1_ratio_reset_timestamp=datetime.now(timezone.utc),
        )

        # Move to 51500 (+2R favorable)
        pos = PositionTracker.update_post_tp1_metrics(pos, Decimal("51500"))

        # Retrace to 50750 (-1R adverse)
        pos = PositionTracker.update_post_tp1_metrics(pos, Decimal("50750"))

        # Ratio should be 2R / 1R = 2.0
        assert pos.ratio_since_tp1 == Decimal("2.0")


class TestUpdateAllMetrics:
    """Test the update_all_metrics convenience method."""

    def test_updates_all_metrics(self, long_position):
        """Test that all metrics are updated in one call."""
        updated = PositionTracker.update_all_metrics(long_position, Decimal("51000"))

        # MFE/MAE updated
        assert updated.mfe == Decimal("1000")

        # R-multiples updated
        assert updated.current_r == Decimal("4.0")  # 1000 / 250

        # Current ratio updated
        assert updated.current_ratio == Decimal("50.0")  # Clean move

    def test_updates_post_tp1_when_applicable(self, long_position):
        """Test post-TP1 metrics updated when TP1 hit."""
        # Mark TP1 hit
        pos = long_position.copy(
            tp1a_hit=True,
            tp1a_price=Decimal("51000"),
            tp1_ratio_reset_timestamp=datetime.now(timezone.utc),
        )

        # Price moves beyond TP1
        updated = PositionTracker.update_all_metrics(pos, Decimal("51500"))

        # All metrics updated
        assert updated.mfe > Decimal("0")
        assert updated.current_r > Decimal("0")
        assert updated.peak_favorable_r_beyond_tp1 > Decimal("0")


class TestPositionQualityAnalyzer:
    """Test position quality analysis."""

    def test_high_quality_detection(self, long_position):
        """Test high quality position detection."""
        # Set high ratio
        pos = long_position.copy(current_ratio=Decimal("4.0"))

        assert PositionQualityAnalyzer.is_high_quality(pos, threshold=Decimal("3.0"))
        assert not PositionQualityAnalyzer.is_low_quality(pos)

    def test_low_quality_detection(self, long_position):
        """Test low quality position detection."""
        # Set low ratio
        pos = long_position.copy(current_ratio=Decimal("1.2"))

        assert PositionQualityAnalyzer.is_low_quality(pos, threshold=Decimal("1.5"))
        assert not PositionQualityAnalyzer.is_high_quality(pos)

    def test_weak_post_tp1_detection(self, long_position):
        """Test weak post-TP1 detection."""
        # TP1 hit but low ratio
        pos = long_position.copy(tp1a_hit=True, ratio_since_tp1=Decimal("1.5"))

        assert PositionQualityAnalyzer.is_weak_post_tp1(
            pos, ratio_threshold=Decimal("2.0")
        )

    def test_weak_post_tp1_not_detected_before_tp1(self, long_position):
        """Test weak post-TP1 not detected if TP1 not hit."""
        pos = long_position.copy(tp1a_hit=False, ratio_since_tp1=Decimal("1.0"))

        assert not PositionQualityAnalyzer.is_weak_post_tp1(pos)


class TestCalculateMFEMAERMultiples:
    """Test MFE/MAE R-multiple calculation."""

    def test_calculate_mfe_mae_r_multiples(self, long_position):
        """Test converting MFE/MAE to R-multiples."""
        # Set MFE/MAE
        pos = long_position.copy(mfe=Decimal("500"), mae=Decimal("125"))  # 2R  # 0.5R

        mfe_r, mae_r = PositionTracker.calculate_mfe_mae_r_multiples(pos)

        assert mfe_r == Decimal("2.0")
        assert mae_r == Decimal("0.5")

    def test_zero_initial_risk_returns_zero(self, long_position):
        """Test zero initial risk returns zero R-multiples."""
        pos = long_position.copy(initial_risk_atr=Decimal("0"))

        mfe_r, mae_r = PositionTracker.calculate_mfe_mae_r_multiples(pos)

        assert mfe_r == Decimal("0")
        assert mae_r == Decimal("0")
