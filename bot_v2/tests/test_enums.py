"""
Tests for models.enums module.
"""

from bot_v2.models.enums import (
    ExecutionMode,
    ExitReason,
    OrderStatus,
    PositionSide,
    PositionStatus,
    SignalType,
    TradeSide,
)


class TestSignalType:
    """Test SignalType enum."""

    def test_signal_types_exist(self):
        """Test all signal types are defined."""
        assert SignalType.LONG.value == "long"
        assert SignalType.SHORT.value == "short"
        assert SignalType.EXIT_LONG.value == "exit_long"
        assert SignalType.EXIT_SHORT.value == "exit_short"

    def test_string_conversion(self):
        """Test string conversion."""
        assert str(SignalType.LONG) == "long"
        assert str(SignalType.SHORT) == "short"


class TestPositionSide:
    """Test PositionSide enum."""

    def test_position_sides_exist(self):
        """Test both position sides are defined."""
        assert PositionSide.LONG.value == "long"
        assert PositionSide.SHORT.value == "short"

    def test_opposite(self):
        """Test opposite() method returns correct opposite side."""
        assert PositionSide.LONG.opposite() == PositionSide.SHORT
        assert PositionSide.SHORT.opposite() == PositionSide.LONG

    def test_string_conversion(self):
        """Test string conversion."""
        assert str(PositionSide.LONG) == "long"
        assert str(PositionSide.SHORT) == "short"


class TestExitReason:
    """Test ExitReason enum."""

    def test_signal_exits(self):
        """Test signal-based exit reasons."""
        assert ExitReason.REVERSAL_SIGNAL.value == "reversal_signal"
        assert ExitReason.MOMENTUM_EXIT.value == "momentum_exit"

    def test_profit_exits(self):
        """Test profit-based exit reasons."""
        assert ExitReason.TAKE_PROFIT_1.value == "take_profit_1"
        assert ExitReason.TAKE_PROFIT_2.value == "take_profit_2"
        assert ExitReason.TAKE_PROFIT_3.value == "take_profit_3"

    def test_stop_exits(self):
        """Test stop-loss exit reasons."""
        assert ExitReason.ATR_STOP_LOSS.value == "atr_stop_loss"
        assert ExitReason.TRAILING_STOP_LOSS.value == "trailing_stop_loss"

    def test_time_exits(self):
        """Test time-based exit reasons."""
        assert ExitReason.MAX_DURATION_REACHED.value == "max_duration_reached"
        assert ExitReason.RATIO_DECAY_EXIT.value == "ratio_decay_exit"


class TestTradeSide:
    """Test TradeSide enum and conversion logic."""

    def test_trade_sides_exist(self):
        """Test both trade sides are defined."""
        assert TradeSide.BUY.value == "buy"
        assert TradeSide.SELL.value == "sell"

    def test_from_position_side_long_entry(self):
        """Test LONG entry converts to BUY."""
        side = TradeSide.from_position_side(PositionSide.LONG, is_entry=True)
        assert side == TradeSide.BUY

    def test_from_position_side_long_exit(self):
        """Test LONG exit converts to SELL."""
        side = TradeSide.from_position_side(PositionSide.LONG, is_entry=False)
        assert side == TradeSide.SELL

    def test_from_position_side_short_entry(self):
        """Test SHORT entry converts to SELL."""
        side = TradeSide.from_position_side(PositionSide.SHORT, is_entry=True)
        assert side == TradeSide.SELL

    def test_from_position_side_short_exit(self):
        """Test SHORT exit converts to BUY."""
        side = TradeSide.from_position_side(PositionSide.SHORT, is_entry=False)
        assert side == TradeSide.BUY


class TestExecutionMode:
    """Test ExecutionMode enum."""

    def test_modes_exist(self):
        """Test both execution modes are defined."""
        assert ExecutionMode.LIVE.value == "live"
        assert ExecutionMode.SIMULATION.value == "local_sim"


class TestPositionStatus:
    """Test PositionStatus enum."""

    def test_statuses_exist(self):
        """Test all position statuses are defined."""
        assert PositionStatus.OPEN.value == "open"
        assert PositionStatus.CLOSED.value == "closed"
        assert PositionStatus.PARTIALLY_CLOSED.value == "partially_closed"


class TestOrderStatus:
    """Test OrderStatus enum."""

    def test_statuses_exist(self):
        """Test all order statuses are defined."""
        assert OrderStatus.PENDING.value == "pending"
        assert OrderStatus.FILLED.value == "filled"
        assert OrderStatus.PARTIALLY_FILLED.value == "partially_filled"
        assert OrderStatus.CANCELLED.value == "cancelled"
        assert OrderStatus.FAILED.value == "failed"
