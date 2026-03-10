"""
Unit tests for Exit Condition Engine

Tests all exit conditions in priority order with comprehensive coverage.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot_v2.exit_engine.engine import ExitConditionEngine
from bot_v2.models.enums import PositionStatus
from bot_v2.models.strategy_config import StrategyConfig


@pytest.fixture
def sample_strategy():
    """Create a sample strategy configuration."""
    return StrategyConfig(
        symbol_id="BTCUSDT",
        mode="local_sim",
        initial_capital=Decimal("1000.0"),
        timeframe="30m",
        soft_sl_atr_mult=Decimal("4.5"),
        hard_sl_atr_mult=Decimal("5.5"),
        catastrophic_stop_mult=Decimal("6.0"),
        tp1a_atr_mult=Decimal("0.7"),
        tp1_atr_mult=Decimal("1.2"),
        trail_sl_atr_mult=Decimal("1.2"),
        trailing_start_r=Decimal("0.85"),
        partial_exit_on_adverse_r=Decimal("2.5"),
        partial_exit_pct=Decimal("50"),
        stale_max_minutes=600,
        stale_min_mfe_r=Decimal("0.3"),
        min_hold_time_hours=Decimal("0.5"),
    )


class TestCatastrophicStop:
    """Test catastrophic stop (Priority 1)."""

    def test_catastrophic_stop_long_triggered(
        self, sample_long_position, sample_strategy
    ):
        """LONG: Catastrophic stop triggers at 6x ATR below entry."""
        pos = sample_long_position
        pos.entry_price = Decimal("100.0")
        pos.initial_risk_atr = Decimal("2.0")

        # Catastrophic stop at 100 - (2.0 * 6.0) = 88.0
        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("88.0"), Decimal("2.0")
        )
        exit_cond = engine.evaluate_all_exits()

        assert exit_cond is not None
        assert exit_cond.reason == "CatastrophicStop"
        assert exit_cond.priority == 1

    def test_catastrophic_stop_short_triggered(
        self, sample_short_position, sample_strategy
    ):
        """SHORT: Catastrophic stop triggers at 6x ATR above entry."""
        pos = sample_short_position
        pos.entry_price = Decimal("100.0")
        pos.initial_risk_atr = Decimal("2.0")

        # Catastrophic stop at 100 + (2.0 * 6.0) = 112.0
        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("112.0"), Decimal("2.0")
        )
        exit_cond = engine.evaluate_all_exits()

        assert exit_cond is not None
        assert exit_cond.reason == "CatastrophicStop"
        assert exit_cond.priority == 1


class TestHardStop:
    """Test hard stop loss (Priority 1)."""

    def test_hard_stop_long_triggered(self, sample_long_position, sample_strategy):
        """LONG: Hard stop triggers at configured price."""
        pos = sample_long_position
        pos.hard_sl_price = Decimal("95.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("94.9"), Decimal("2.0")
        )
        exit_cond = engine.evaluate_all_exits()

        assert exit_cond is not None
        assert exit_cond.reason == "HardSL"
        assert exit_cond.priority == 2

    def test_hard_stop_not_triggered_above(self, sample_long_position, sample_strategy):
        """LONG: Hard stop not triggered if price above stop."""
        pos = sample_long_position
        pos.hard_sl_price = Decimal("95.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("96.0"), Decimal("2.0")
        )
        exit_cond = engine._check_hard_stop()

        assert exit_cond is None


class TestSoftStop:
    """Test soft stop / breakeven (Priority 2)."""

    def test_soft_stop_triggers_after_1_minute(
        self, sample_long_position, sample_strategy
    ):
        """Soft stop triggers after 1-minute buffer."""
        pos = sample_long_position
        pos.entry_time = datetime.now(timezone.utc) - timedelta(minutes=2)
        pos.soft_sl_price = Decimal("97.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("96.9"), Decimal("2.0")
        )
        exit_cond = engine._check_soft_sl_continuous()

        assert exit_cond is not None
        assert exit_cond.reason == "SoftSL"

    def test_soft_stop_not_triggered_within_1_minute(
        self, sample_long_position, sample_strategy
    ):
        """Soft stop does not trigger within 1-minute buffer."""
        pos = sample_long_position
        pos.entry_time = datetime.now(timezone.utc) - timedelta(seconds=30)
        pos.soft_sl_price = Decimal("97.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("96.9"), Decimal("2.0")
        )
        exit_cond = engine._check_soft_sl_continuous()

        assert exit_cond is None

    def test_breakeven_stop_identified(self, sample_long_position, sample_strategy):
        """Breakeven stop correctly identified when moved_to_breakeven flag set."""
        pos = sample_long_position
        pos.entry_time = datetime.now(timezone.utc) - timedelta(minutes=2)
        pos.soft_sl_price = Decimal("99.0")
        pos.moved_to_breakeven = True

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("98.9"), Decimal("2.0")
        )
        exit_cond = engine._check_soft_sl_continuous()

        assert exit_cond is not None
        assert exit_cond.reason == "BreakevenStop"


class TestTrailingStop:
    """Test trailing stop (Priority 3)."""

    def test_trailing_stop_long_triggered(self, sample_long_position, sample_strategy):
        """LONG: Trailing stop triggers when price hits trailing price."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.trailing_sl_price = Decimal("105.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("104.9"), Decimal("2.0")
        )
        exit_cond = engine._check_trailing_stop()

        assert exit_cond is not None
        assert exit_cond.reason == "TrailExit"
        assert exit_cond.priority == 6

    def test_aggressive_peak_exit_clean_trade(
        self, sample_long_position, sample_strategy
    ):
        """Aggressive peak exit triggers on 15% pullback for clean trades."""
        pos = sample_long_position
        pos.mfe = Decimal("10.0")
        pos.mae = Decimal("1.0")  # Ratio = 10 (extreme quality)
        pos.peak_favorable_r = Decimal("2.0")
        pos.current_r = Decimal("1.6")  # 20% pullback from peak (exceeds 15% threshold)
        pos.tp1a_hit = True  # APE only active after TP1a

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("110.0"), Decimal("2.0")
        )
        exit_cond = engine._check_trailing_stop()

        assert exit_cond is not None
        assert exit_cond.reason == "AggressivePeakExit"

    def test_aggressive_peak_exit_not_triggered_small_pullback(
        self, sample_long_position, sample_strategy
    ):
        """Aggressive peak exit not triggered on <15% pullback."""
        pos = sample_long_position
        pos.mfe = Decimal("10.0")
        pos.mae = Decimal("1.0")  # Ratio = 10
        pos.peak_favorable_r = Decimal("2.0")
        pos.current_r = Decimal("1.75")  # 12.5% pullback (< 15% threshold)
        pos.tp1a_hit = True

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("110.0"), Decimal("2.0")
        )
        exit_cond = engine._check_trailing_stop()

        assert exit_cond is None or exit_cond.reason != "AggressivePeakExit"


class TestTakeProfitTargets:
    """Test TP1 targets (Priority 4)."""

    def test_tp1a_scalp_triggered(self, sample_long_position, sample_strategy):
        """TP1a scalp triggers at quick target (30% close)."""
        pos = sample_long_position
        pos.tp1a_hit = False
        pos.tp1a_price = Decimal("105.0")
        pos.current_amount = Decimal("1.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("105.1"), Decimal("2.0")
        )
        exit_cond = engine._check_tp1()

        assert exit_cond is not None
        assert exit_cond.reason == "TP1a"
        assert exit_cond.priority == 4
        # 30% of 1.0 = 0.3
        assert exit_cond.amount == Decimal("0.3")

    def test_tp1b_after_tp1a_hit(self, sample_long_position, sample_strategy):
        """TP1b triggers after TP1a already hit (remaining position)."""
        pos = sample_long_position
        pos.tp1a_hit = True
        pos.tp1_price = Decimal("110.0")
        pos.current_amount = Decimal("0.7")  # Remaining after TP1a

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("110.1"), Decimal("2.0")
        )
        exit_cond = engine._check_tp1()

        assert exit_cond is not None
        assert exit_cond.reason == "TP1b"
        assert exit_cond.amount == Decimal("0.7")  # Close remaining

    def test_tp1b_disabled_when_flag_false(self, sample_long_position, sample_strategy):
        """TP1b should not trigger when tp1_enabled is False."""
        pos = sample_long_position
        pos.tp1a_hit = True
        pos.tp1_price = Decimal("110.0")
        pos.current_amount = Decimal("0.7")

        sample_strategy.tp1_enabled = False

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("110.1"), Decimal("2.0")
        )
        exit_cond = engine._check_tp1()

        assert exit_cond is None

    def test_tp1a_not_triggered_before_target(
        self, sample_long_position, sample_strategy
    ):
        """TP1a not triggered if price below target."""
        pos = sample_long_position
        pos.tp1a_hit = False
        pos.tp1a_price = Decimal("105.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("104.9"), Decimal("2.0")
        )
        exit_cond = engine._check_tp1()

        assert exit_cond is None


class TestAdverseScaleout:
    """Test adverse scale-out (Priority 5)."""

    def test_adverse_scaleout_triggered(self, sample_long_position, sample_strategy):
        """Adverse scale-out triggers when MAE exceeds threshold."""
        pos = sample_long_position
        pos.status = PositionStatus.OPEN
        pos.scaled_out_on_adverse = False
        pos.mae = Decimal("5.0")  # MAE
        pos.initial_risk_atr = Decimal("2.0")  # MAE_R = 5/2 = 2.5R (= threshold)
        pos.current_amount = Decimal("1.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("95.0"), Decimal("2.0")
        )
        exit_cond = engine._check_adverse_scaleout()

        assert exit_cond is not None
        assert exit_cond.reason == "AdverseScaleOut"
        assert exit_cond.priority == 5
        # 50% of 1.0 = 0.5
        assert exit_cond.amount == Decimal("0.5")

    def test_adverse_scaleout_not_triggered_below_threshold(
        self, sample_long_position, sample_strategy
    ):
        """Adverse scale-out not triggered if MAE below threshold."""
        pos = sample_long_position
        pos.status = PositionStatus.OPEN
        pos.scaled_out_on_adverse = False
        pos.mae = Decimal("4.0")  # MAE_R = 4/2 = 2.0R (< 2.5R threshold)
        pos.initial_risk_atr = Decimal("2.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("96.0"), Decimal("2.0")
        )
        exit_cond = engine._check_adverse_scaleout()

        assert exit_cond is None

    def test_adverse_scaleout_not_triggered_if_already_scaled(
        self, sample_long_position, sample_strategy
    ):
        """Adverse scale-out only triggers once per position."""
        pos = sample_long_position
        pos.status = PositionStatus.OPEN
        pos.scaled_out_on_adverse = True  # Already scaled out
        pos.mae = Decimal("5.0")
        pos.initial_risk_atr = Decimal("2.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("95.0"), Decimal("2.0")
        )
        exit_cond = engine._check_adverse_scaleout()

        assert exit_cond is None


class TestStaleTrade:
    """Test stale trade exit (Priority 6)."""

    def test_stale_trade_exit_low_mfe(self, sample_long_position, sample_strategy):
        """Stale trade exits after 600 minutes with MFE < 0.3R."""
        pos = sample_long_position
        pos.status = PositionStatus.OPEN
        pos.entry_time = datetime.now(timezone.utc) - timedelta(minutes=601)
        pos.mfe = Decimal("0.4")  # MFE_R = 0.4/2 = 0.2R (< 0.3R threshold)
        pos.initial_risk_atr = Decimal("2.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("100.5"), Decimal("2.0")
        )
        exit_cond = engine._check_stale_trade()

        assert exit_cond is not None
        assert exit_cond.reason == "StaleTrade"
        assert exit_cond.priority == 8

    def test_stale_trade_exit_flat_price(self, sample_long_position, sample_strategy):
        """Stale trade exits if price is flat (near entry)."""
        pos = sample_long_position
        pos.status = PositionStatus.OPEN
        pos.entry_time = datetime.now(timezone.utc) - timedelta(minutes=601)
        pos.entry_price = Decimal("100.0")
        pos.initial_risk_atr = Decimal("2.0")
        pos.mfe = Decimal("1.0")  # MFE_R = 0.5R (sufficient)

        # Price at 100.05 (within 0.1 * 2.0 = 0.2, so flat)
        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("100.05"), Decimal("2.0")
        )
        exit_cond = engine._check_stale_trade()

        assert exit_cond is not None
        assert exit_cond.reason == "StaleTrade"

    def test_stale_trade_not_triggered_before_time(
        self, sample_long_position, sample_strategy
    ):
        """Stale trade not triggered before stale_max_minutes."""
        pos = sample_long_position
        pos.status = PositionStatus.OPEN
        pos.entry_time = datetime.now(timezone.utc) - timedelta(minutes=500)  # < 600
        pos.mfe = Decimal("0.2")
        pos.initial_risk_atr = Decimal("2.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("100.0"), Decimal("2.0")
        )
        exit_cond = engine._check_stale_trade()

        assert exit_cond is None

    def test_absolute_stale_exit_safety_net(
        self, sample_long_position, sample_strategy
    ):
        """Absolute stale exit triggers at 1.1x stale time (safety net)."""
        pos = sample_long_position
        pos.status = PositionStatus.OPEN
        pos.entry_time = datetime.now(timezone.utc) - timedelta(
            minutes=661
        )  # 1.1 * 600
        pos.mfe = Decimal("2.0")  # High MFE (would normally not exit)
        pos.initial_risk_atr = Decimal("2.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("105.0"), Decimal("2.0")
        )
        exit_cond = engine._check_stale_trade()

        assert exit_cond is not None
        assert exit_cond.reason == "AbsoluteStaleExit"


class TestMinimumHoldTime:
    """Test minimum hold time requirement."""

    def test_min_hold_time_blocks_profit_exits(
        self, sample_long_position, sample_strategy
    ):
        """Min hold time blocks profit-taking exits."""
        pos = sample_long_position
        pos.entry_time = datetime.now(timezone.utc) - timedelta(
            minutes=15
        )  # < 30 min (0.5 hr)
        pos.tp1a_price = Decimal("105.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("105.1"), Decimal("2.0")
        )
        exit_cond = engine.evaluate_all_exits()

        # Should not exit (min hold time not met)
        assert exit_cond is None or exit_cond.reason not in ["TP1a", "TP1b"]

    def test_min_hold_time_allows_safety_exits(
        self, sample_long_position, sample_strategy
    ):
        """Min hold time does NOT block safety exits (hard SL)."""
        pos = sample_long_position
        pos.entry_time = datetime.now(timezone.utc) - timedelta(
            minutes=1
        )  # Very recent
        pos.hard_sl_price = Decimal("95.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("94.9"), Decimal("2.0")
        )
        exit_cond = engine.evaluate_all_exits()

        # Should exit (safety overrides min hold time)
        assert exit_cond is not None
        assert exit_cond.reason == "HardSL"


class TestPriorityOrder:
    """Test exit priority order (safety before profit)."""

    def test_hard_stop_overrides_tp1(self, sample_long_position, sample_strategy):
        """Hard stop has priority over TP1 if both triggered."""
        pos = sample_long_position
        pos.hard_sl_price = Decimal("95.0")
        pos.tp1a_price = Decimal("105.0")
        pos.entry_time = datetime.now(timezone.utc) - timedelta(hours=1)  # Min hold met

        # Price somehow hits both (edge case)
        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("95.0"), Decimal("2.0")
        )
        exit_cond = engine.evaluate_all_exits()

        # Hard stop should win (higher priority)
        assert exit_cond is not None
        assert exit_cond.reason == "HardSL"

    def test_trailing_stop_before_tp1(self, sample_long_position, sample_strategy):
        """Trailing stop checked before TP1."""
        pos = sample_long_position
        pos.is_trailing_active = True
        pos.trailing_sl_price = Decimal("103.0")
        pos.tp1a_price = Decimal("105.0")
        pos.entry_time = datetime.now(timezone.utc) - timedelta(hours=1)

        # Price at 103.0 (hits trailing, below TP1)
        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("103.0"), Decimal("2.0")
        )
        exit_cond = engine.evaluate_all_exits()

        assert exit_cond is not None
        assert exit_cond.reason == "TrailExit"


class TestShortPositions:
    """Test exit conditions for SHORT positions."""

    def test_short_hard_stop_triggered(self, sample_short_position, sample_strategy):
        """SHORT: Hard stop triggers when price goes up."""
        pos = sample_short_position
        pos.hard_sl_price = Decimal("107.0")

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("107.0"), Decimal("2.0")
        )
        exit_cond = engine.evaluate_all_exits()

        assert exit_cond is not None
        assert exit_cond.reason == "HardSL"


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_no_exit_when_price_in_safe_zone(
        self, sample_long_position, sample_strategy
    ):
        """No exit when price is between soft SL and TP1."""
        pos = sample_long_position
        pos.soft_sl_price = Decimal("95.0")
        pos.tp1_price = Decimal("110.0")
        pos.entry_time = datetime.now(timezone.utc) - timedelta(hours=1)

        # Price safely between stops and targets
        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("102.0"), Decimal("2.0")
        )
        exit_cond = engine.evaluate_all_exits()

        assert exit_cond is None

    def test_multiple_exits_triggered_highest_priority_wins(
        self, sample_long_position, sample_strategy
    ):
        """When multiple exits trigger, highest priority (lowest number) wins."""
        pos = sample_long_position
        pos.hard_sl_price = Decimal("93.0")
        pos.tp1_price = Decimal("93.0")  # Both at same price
        pos.entry_time = datetime.now(timezone.utc) - timedelta(hours=1)

        # At price where both hard SL and TP1 would trigger
        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("93.0"), Decimal("2.0")
        )
        exit_cond = engine.evaluate_all_exits()

        # Hard SL (priority 1) should win over TP1 (priority 4)
        assert exit_cond is not None
        assert exit_cond.reason == "HardSL"

    def test_adverse_scaleout_once_only(self, sample_long_position, sample_strategy):
        """Adverse scale-out should not trigger if already done."""
        pos = sample_long_position
        pos.scaled_out_on_adverse = True  # Already scaled out
        pos.peak_adverse_r = Decimal("3.0")  # Well past threshold

        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("100.0"), Decimal("2.0")
        )
        exit_cond = engine._check_adverse_scaleout()

        assert exit_cond is None  # Should not trigger again


class TestPostTP1Quality:
    """Test post-TP1 quality tracking for aggressive peak exit."""

    def test_aggressive_peak_uses_high_ratio(
        self, sample_long_position, sample_strategy
    ):
        """Aggressive peak exit should trigger for high-quality trades."""
        pos = sample_long_position
        pos.peak_favorable_r = Decimal("6.0")  # High quality (>5.0)
        pos.peak_price_since_entry = Decimal("130.0")
        pos.entry_time = datetime.now(timezone.utc) - timedelta(hours=2)
        pos.is_trailing_active = True
        pos.tp1a_hit = True

        # 3.5% pullback from peak (130 -> 125.5)
        engine = ExitConditionEngine(
            pos, sample_strategy, Decimal("125.5"), Decimal("2.0")
        )
        exit_cond = engine._check_trailing_stop()

        assert exit_cond is not None
        assert exit_cond.reason == "AggressivePeakExit"


class TestAdverseScaleoutGracePeriod:
    """Test grace period after adverse scale-out prevents immediate soft SL trigger."""

    def test_soft_sl_blocked_during_grace_period(self, sample_strategy):
        """Soft SL should NOT trigger during grace period after adverse scale-out."""
        from bot_v2.exit_engine.engine_v1 import ExitConditionEngine as EngineV1
        from bot_v2.models.position_v1 import PositionState
        from bot_v2.models.position_v1 import TradeSide as TradeSideV1

        # Create PositionState (v1) directly
        pos = PositionState(
            symbol_id="BTCUSDT",
            side=TradeSideV1.BUY,
            entry_price=Decimal("100.0"),
            current_amount=Decimal("1.0"),
            initial_amount=Decimal("1.0"),
            entry_time=datetime.now(timezone.utc) - timedelta(hours=1),
            entry_atr=Decimal("2.0"),
            initial_risk_atr=Decimal("2.0"),
            soft_sl_price=Decimal("100.0"),  # Moved to breakeven after scale-out
            hard_sl_price=Decimal("89.0"),
            tp1_price=Decimal("104.0"),
            total_entry_fee=Decimal("0.05"),
            scaled_out_on_adverse=True,
            adverse_scaleout_timestamp=datetime.now(timezone.utc)
            - timedelta(seconds=60),  # 1 minute ago
        )

        # Add grace period config
        sample_strategy.scaleout_grace_period_seconds = 300  # 5 minutes

        # Price below soft SL, but within grace period
        current_price = Decimal("99.5")
        engine = EngineV1(pos, sample_strategy, current_price, Decimal("2.0"))
        exit_cond = engine._check_soft_sl_continuous()

        # Should NOT trigger due to grace period
        assert exit_cond is None

    def test_soft_sl_triggers_after_grace_period(self, sample_strategy):
        """Soft SL should trigger after grace period expires."""
        from bot_v2.exit_engine.engine_v1 import ExitConditionEngine as EngineV1
        from bot_v2.models.position_v1 import PositionState
        from bot_v2.models.position_v1 import TradeSide as TradeSideV1

        pos = PositionState(
            symbol_id="BTCUSDT",
            side=TradeSideV1.BUY,
            entry_price=Decimal("100.0"),
            current_amount=Decimal("1.0"),
            initial_amount=Decimal("1.0"),
            entry_time=datetime.now(timezone.utc) - timedelta(hours=1),
            entry_atr=Decimal("2.0"),
            initial_risk_atr=Decimal("2.0"),
            soft_sl_price=Decimal("100.0"),  # Moved to breakeven after scale-out
            hard_sl_price=Decimal("89.0"),
            tp1_price=Decimal("104.0"),
            total_entry_fee=Decimal("0.05"),
            scaled_out_on_adverse=True,
            adverse_scaleout_timestamp=datetime.now(timezone.utc)
            - timedelta(seconds=400),  # 6.67 minutes ago
        )

        # Add grace period config
        sample_strategy.scaleout_grace_period_seconds = 300  # 5 minutes

        # Price below soft SL, grace period expired
        current_price = Decimal("99.5")
        engine = EngineV1(pos, sample_strategy, current_price, Decimal("2.0"))
        exit_cond = engine._check_soft_sl_continuous()

        # Should trigger now
        assert exit_cond is not None
        assert exit_cond.name in ["BreakevenStop", "SoftSL"]

    def test_hard_sl_works_during_grace_period(self, sample_strategy):
        """Hard SL should still trigger during grace period (safety override)."""
        from bot_v2.exit_engine.engine_v1 import ExitConditionEngine as EngineV1
        from bot_v2.models.position_v1 import PositionState
        from bot_v2.models.position_v1 import TradeSide as TradeSideV1

        pos = PositionState(
            symbol_id="BTCUSDT",
            side=TradeSideV1.BUY,
            entry_price=Decimal("100.0"),
            current_amount=Decimal("1.0"),
            initial_amount=Decimal("1.0"),
            entry_time=datetime.now(timezone.utc) - timedelta(hours=1),
            entry_atr=Decimal("2.0"),
            initial_risk_atr=Decimal("2.0"),
            soft_sl_price=Decimal("100.0"),
            hard_sl_price=Decimal("89.0"),  # 5.5 ATR below entry
            tp1_price=Decimal("104.0"),
            total_entry_fee=Decimal("0.05"),
            scaled_out_on_adverse=True,
            adverse_scaleout_timestamp=datetime.now(timezone.utc)
            - timedelta(seconds=60),  # Within grace period
        )

        sample_strategy.scaleout_grace_period_seconds = 300

        # Price hits hard stop during grace period
        # Note: catastrophic stop is at 6.0 ATR (100 - 12 = 88.0), hard stop at 89.0
        # Use 88.5 to be below hard stop but above catastrophic stop
        current_price = Decimal("88.5")
        engine = EngineV1(pos, sample_strategy, current_price, Decimal("2.0"))

        # Hard stop has Priority 2, checked after catastrophic stop but before soft SL
        exit_cond = engine.evaluate_all_exits()

        # Should trigger hard stop despite grace period
        assert exit_cond is not None
        assert exit_cond.name == "HardSL"

    def test_normal_position_unaffected_by_grace_period(self, sample_strategy):
        """Normal positions without adverse scale-out should have standard soft SL behavior."""
        from bot_v2.exit_engine.engine_v1 import ExitConditionEngine as EngineV1
        from bot_v2.models.position_v1 import PositionState
        from bot_v2.models.position_v1 import TradeSide as TradeSideV1

        pos = PositionState(
            symbol_id="BTCUSDT",
            side=TradeSideV1.BUY,
            entry_price=Decimal("100.0"),
            current_amount=Decimal("1.0"),
            initial_amount=Decimal("1.0"),
            entry_time=datetime.now(timezone.utc) - timedelta(hours=1),
            entry_atr=Decimal("2.0"),
            initial_risk_atr=Decimal("2.0"),
            soft_sl_price=Decimal("91.0"),  # Normal soft SL at 4.5 ATR
            hard_sl_price=Decimal("89.0"),
            tp1_price=Decimal("104.0"),
            total_entry_fee=Decimal("0.05"),
            scaled_out_on_adverse=False,  # No scale-out
            adverse_scaleout_timestamp=None,
        )

        sample_strategy.scaleout_grace_period_seconds = 300

        # Price below soft SL
        current_price = Decimal("90.0")
        engine = EngineV1(pos, sample_strategy, current_price, Decimal("2.0"))
        exit_cond = engine._check_soft_sl_continuous()

        # Should trigger immediately (no grace period for normal positions)
        assert exit_cond is not None
        assert exit_cond.name == "SoftSL"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
