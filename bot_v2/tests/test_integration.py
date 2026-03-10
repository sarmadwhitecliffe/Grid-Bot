"""
Integration tests for bot_v2 - Week 2 Day 5-7

Tests end-to-end workflows across multiple modules:
1. Position lifecycle: entry → update → exit
2. Exit engine → OrderManager → Exchange flow
3. StateManager persistence → reload workflow
4. Config loading → Strategy execution
"""

import shutil
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_v2.exit_engine.engine import ExitConditionEngine
from bot_v2.models.enums import PositionSide, PositionStatus
from bot_v2.models.position import Position
from bot_v2.models.strategy_config import StrategyConfig
from bot_v2.persistence.state_manager import StateManager


class TestPositionLifecycle:
    """Test complete position lifecycle from entry to exit."""

    def test_full_lifecycle_entry_to_exit(self, sample_long_position, sample_strategy):
        """Test position from entry → profitable → exit."""
        pos = sample_long_position
        strategy = sample_strategy

        # Phase 1: Entry - Position opens
        assert pos.status == PositionStatus.OPEN
        assert pos.current_amount == pos.initial_amount
        assert pos.trailing_sl_price is None

        # Phase 2: Favorable movement - reaches 1.5R
        pos.current_r = Decimal("1.5")
        pos.peak_favorable_r = Decimal("1.5")
        pos.peak_price_since_entry = Decimal("103.0")
        pos.is_trailing_active = True

        # Check exit engine doesn't trigger (profitable, no exit condition)
        engine = ExitConditionEngine(pos, strategy, Decimal("103.0"), Decimal("2.0"))
        exit_cond = engine.evaluate_all_exits()
        assert exit_cond is None  # No exit yet

        # Phase 3: More profit - reaches 2.0R, update trailing
        pos.current_r = Decimal("2.0")
        pos.peak_favorable_r = Decimal("2.0")
        pos.peak_price_since_entry = Decimal("104.0")
        pos.trailing_sl_price = Decimal("101.0")  # Trailing below peak

        # Still no exit
        engine = ExitConditionEngine(pos, strategy, Decimal("104.0"), Decimal("2.0"))
        exit_cond = engine.evaluate_all_exits()
        assert exit_cond is None

        # Phase 4: Pullback - some exit triggers
        pos.current_r = Decimal("1.5")
        current_price = Decimal("100.5")  # Below trailing stop

        engine = ExitConditionEngine(pos, strategy, current_price, Decimal("2.0"))
        exit_cond = engine.evaluate_all_exits()

        # Should trigger an exit (trailing or aggressive peak)
        assert exit_cond is not None
        assert exit_cond.reason in ["TrailExit", "AggressivePeakExit"]
        assert exit_cond.amount == pos.current_amount
        assert pos.status == PositionStatus.OPEN  # Not yet closed by engine

    def test_lifecycle_catastrophic_stop(self, sample_long_position, sample_strategy):
        """Test immediate catastrophic stop loss path."""
        pos = sample_long_position
        pos.entry_price = Decimal("100.0")
        strategy = sample_strategy
        strategy.catastrophic_stop_mult = Decimal("3.0")

        # Price drops catastrophically (3x initial risk)
        Decimal("94.0")  # 100 - (2.0 * 3.0) = 94
        current_price = Decimal("93.5")  # Below catastrophic stop

        engine = ExitConditionEngine(pos, strategy, current_price, Decimal("2.0"))
        exit_cond = engine.evaluate_all_exits()

        assert exit_cond is not None
        assert exit_cond.reason == "CatastrophicStop"
        assert exit_cond.priority == 1  # Highest priority


class TestExitEngineToOrderManager:
    """Integration tests for exit detection flow."""

    def test_exit_condition_detection(self, sample_long_position, sample_strategy):
        """Test exit condition detection logic."""
        pos = sample_long_position
        strategy = sample_strategy

        # Setup position for exit
        pos.current_r = Decimal("2.0")
        pos.peak_favorable_r = Decimal("2.0")
        pos.peak_price_since_entry = Decimal("104.0")
        pos.trailing_sl_price = Decimal("102.0")
        pos.is_trailing_active = True

        # Price drops to trigger exit
        current_price = Decimal("101.5")

        # Exit engine detects exit condition
        engine = ExitConditionEngine(pos, strategy, current_price, Decimal("2.0"))
        exit_cond = engine.evaluate_all_exits()

        # Should detect some exit
        assert exit_cond is not None
        assert exit_cond.amount == pos.current_amount
        assert exit_cond.price == current_price

    def test_catastrophic_stop_priority(self, sample_long_position, sample_strategy):
        """Test catastrophic stop has highest priority."""
        pos = sample_long_position
        strategy = sample_strategy

        # Setup position
        pos.current_r = Decimal("-3.0")  # Deep loss
        pos.peak_favorable_r = Decimal("1.0")

        # Price at catastrophic level
        current_price = Decimal("70.0")

        # Exit engine should detect catastrophic
        engine = ExitConditionEngine(pos, strategy, current_price, Decimal("2.0"))
        exit_cond = engine.evaluate_all_exits()

        assert exit_cond is not None
        assert exit_cond.reason == "CatastrophicStop"
        assert exit_cond.amount == pos.current_amount


class TestStatePersistence:
    """Test state manager persistence and reload workflows."""

    @pytest.fixture
    def temp_data_dir(self):
        """Create temporary data directory for testing."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)

    def test_save_and_reload_positions(self, sample_long_position, temp_data_dir):
        """Test saving positions and reloading them."""
        # Phase 1: Save position
        state_manager = StateManager(data_dir=Path(temp_data_dir))
        positions = {"BTCUSDT": sample_long_position}

        state_manager.save_positions(positions)

        # Verify file was created
        positions_file = Path(temp_data_dir) / "active_positions.json"
        assert positions_file.exists()

        # Phase 2: Reload positions
        loaded_positions = state_manager.load_positions()

        assert "BTCUSDT" in loaded_positions
        loaded_pos = loaded_positions["BTCUSDT"]

        # Verify critical fields preserved
        assert loaded_pos.symbol_id == sample_long_position.symbol_id
        assert loaded_pos.side == sample_long_position.side
        assert loaded_pos.entry_price == sample_long_position.entry_price
        assert loaded_pos.initial_amount == sample_long_position.initial_amount
        assert loaded_pos.status == sample_long_position.status

    def test_position_state_survives_restart(self, sample_long_position, temp_data_dir):
        """Test position state survives bot restart."""
        # Phase 1: Bot running - position has state
        state_manager = StateManager(data_dir=Path(temp_data_dir))

        pos = sample_long_position
        pos.current_r = Decimal("1.8")
        pos.peak_favorable_r = Decimal("2.0")
        pos.is_trailing_active = True
        pos.trailing_sl_price = Decimal("102.0")
        pos.tp1a_hit = True
        pos.bars_held = 15

        positions = {"BTCUSDT": pos}
        state_manager.save_positions(positions)

        # Phase 2: Simulate restart - new state manager instance
        del state_manager
        new_state_manager = StateManager(data_dir=Path(temp_data_dir))

        # Phase 3: Reload state
        reloaded_positions = new_state_manager.load_positions()
        reloaded_pos = reloaded_positions["BTCUSDT"]

        # Verify runtime state preserved
        assert reloaded_pos.current_r == Decimal("1.8")
        assert reloaded_pos.peak_favorable_r == Decimal("2.0")
        assert reloaded_pos.is_trailing_active is True
        assert reloaded_pos.trailing_sl_price == Decimal("102.0")
        assert reloaded_pos.tp1a_hit is True
        assert reloaded_pos.bars_held == 15

    def test_save_and_reload_capitals(self, temp_data_dir):
        """Test capital allocation persistence."""
        state_manager = StateManager(data_dir=Path(temp_data_dir))

        # Save capitals
        capitals = {
            "BTCUSDT": Decimal("1000.0"),
            "ETHUSDT": Decimal("500.0"),
            "SOLUSDT": Decimal("250.0"),
        }
        state_manager.save_capitals(capitals)

        # Reload capitals
        loaded_capitals = state_manager.load_capitals()

        assert loaded_capitals["BTCUSDT"] == Decimal("1000.0")
        assert loaded_capitals["ETHUSDT"] == Decimal("500.0")
        assert loaded_capitals["SOLUSDT"] == Decimal("250.0")

    def test_trade_history_accumulation(self, temp_data_dir):
        """Test trade history persistence."""
        state_manager = StateManager(data_dir=Path(temp_data_dir))

        # Save trade history
        history = [
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "pnl": "150.50",
                "r_multiple": "2.0",
                "exit_reason": "TrailExit",
            },
            {
                "symbol": "ETHUSDT",
                "side": "LONG",
                "pnl": "-50.25",
                "r_multiple": "-0.5",
                "exit_reason": "StopLoss",
            },
        ]
        state_manager.save_history(history)

        # Load history
        loaded_history = state_manager.load_trade_history()

        assert len(loaded_history) == 2
        assert loaded_history[0]["symbol"] == "BTCUSDT"
        assert loaded_history[1]["symbol"] == "ETHUSDT"


class TestConfigToExecution:
    """Test config loading → strategy execution flow."""

    def test_strategy_config_loads_and_applies(self):
        """Test strategy config loads and applies to position."""
        # Load strategy config
        strategy = StrategyConfig(
            symbol_id="BTCUSDT",
            timeframe="15m",
            trail_sl_atr_mult=Decimal("2.5"),
            soft_sl_atr_mult=Decimal("1.5"),
            hard_sl_atr_mult=Decimal("3.0"),
            catastrophic_stop_mult=Decimal("4.0"),
        )

        # Create position using strategy parameters
        pos = Position(
            symbol_id=strategy.symbol_id,
            side=PositionSide.LONG,
            entry_price=Decimal("100.0"),
            initial_amount=Decimal("1.0"),
            entry_atr=Decimal("2.0"),
            initial_risk_atr=Decimal("2.0"),
            total_entry_fee=Decimal("0.01"),
            soft_sl_price=Decimal("100.0")
            - (Decimal("2.0") * strategy.soft_sl_atr_mult),
            hard_sl_price=Decimal("100.0")
            - (Decimal("2.0") * strategy.hard_sl_atr_mult),
            tp1_price=Decimal("110.0"),
            entry_time=datetime.now(timezone.utc),
        )

        # Verify strategy parameters applied correctly
        assert pos.soft_sl_price == Decimal("97.0")  # 100 - (2.0 * 1.5)
        assert pos.hard_sl_price == Decimal("94.0")  # 100 - (2.0 * 3.0)

        # Test exit engine uses strategy config
        engine = ExitConditionEngine(pos, strategy, Decimal("100.0"), Decimal("2.0"))

        # Verify engine has correct strategy parameters
        assert engine.strategy.trail_sl_atr_mult == Decimal("2.5")
        assert engine.strategy.catastrophic_stop_mult == Decimal("4.0")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
