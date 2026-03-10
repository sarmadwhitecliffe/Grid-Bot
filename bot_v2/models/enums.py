"""
Enumerations for type safety and clarity.

Extracted from bot.py to eliminate code duplication and provide
a single source of truth for all type definitions.
"""

from enum import Enum


class SignalType(Enum):
    """Trading signal types."""

    LONG = "long"
    SHORT = "short"
    EXIT_LONG = "exit_long"
    EXIT_SHORT = "exit_short"

    def __str__(self) -> str:
        return self.value


class PositionSide(Enum):
    """Position direction."""

    LONG = "long"
    SHORT = "short"

    def __str__(self) -> str:
        return self.value

    def opposite(self) -> "PositionSide":
        """Return the opposite side."""
        return PositionSide.SHORT if self == PositionSide.LONG else PositionSide.LONG


class ExitReason(Enum):
    """Reasons for exiting a position."""

    # Priority 100: Signal-based exits
    REVERSAL_SIGNAL = "reversal_signal"
    MOMENTUM_EXIT = "momentum_exit"

    # Priority 90: Profit targets
    TAKE_PROFIT_1 = "take_profit_1"  # 1%
    TAKE_PROFIT_2 = "take_profit_2"  # 1.5%
    TAKE_PROFIT_3 = "take_profit_3"  # 2%
    ATR_PROFIT_2X = "atr_profit_2x"
    ATR_PROFIT_3X = "atr_profit_3x"
    REENTRY_PROFIT_TARGET = "reentry_profit_target"

    # Priority 80: Stop losses
    ATR_STOP_LOSS = "atr_stop_loss"
    TRAILING_STOP_LOSS = "trailing_stop_loss"
    MAX_LOSS_STOP = "max_loss_stop"

    # Priority 70: Time-based exits
    MAX_DURATION_REACHED = "max_duration_reached"
    RATIO_DECAY_EXIT = "ratio_decay_exit"

    # Manual/Admin exits
    MANUAL_EXIT = "manual_exit"
    SYSTEM_SHUTDOWN = "system_shutdown"
    ERROR = "error"

    def __str__(self) -> str:
        return self.value


class ExecutionMode(Enum):
    """Trade execution mode."""

    LIVE = "live"
    SIMULATION = "local_sim"

    def __str__(self) -> str:
        return self.value


class PositionStatus(Enum):
    """Position lifecycle status."""

    OPEN = "open"
    CLOSED = "closed"
    PARTIALLY_CLOSED = "partially_closed"

    def __str__(self) -> str:
        return self.value


class OrderStatus(Enum):
    """Order execution status."""

    PENDING = "pending"
    NEW = "new"  # Order placed, awaiting fill
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNVERIFIED = "unverified"  # Order placed but verification failed
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


class TradeSide(Enum):
    """Trade direction (buy/sell) - used for exchange orders."""

    BUY = "buy"
    SELL = "sell"

    def __str__(self) -> str:
        return self.value

    @staticmethod
    def from_position_side(position_side: PositionSide, is_entry: bool) -> "TradeSide":
        """
        Convert position side to trade side.

        Args:
            position_side: The position side (LONG or SHORT)
            is_entry: True for entry, False for exit

        Returns:
            BUY or SELL
        """
        if position_side == PositionSide.LONG:
            return TradeSide.BUY if is_entry else TradeSide.SELL
        else:  # SHORT
            return TradeSide.SELL if is_entry else TradeSide.BUY


class PostTP1State(Enum):
    """Post-TP1 trailing stop states to resolve logic conflicts."""

    NOT_HIT = "not_hit"
    PROBATION = "probation"  # First 2 minutes after TP1a
    WEAK_TRADE = "weak_trade"  # Probation expired, ratio < 3.0
    MOMENTUM_DECAY = "momentum_decay"  # Significant giveback from post-TP1 peak
    RATIO_DECAY = "ratio_decay"  # Traditional R-decay detection
    NORMAL_TRAILING = "normal_trailing"  # Standard post-TP1 trailing

    def __str__(self) -> str:
        return self.value
